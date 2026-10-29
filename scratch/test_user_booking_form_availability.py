import os
import sys
import unittest
from datetime import date, timedelta
from bson import ObjectId

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from utils.allocation_logic import load_settings, allocate_raft
from utils.effective_availability import calculate_effective_available_capacity


class TestUserBookingFormAvailability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.client = cls.app.test_client()
        cls.db = cls.app.mongo.db

        try:
            cls.db.command('ping')
        except Exception as e:
            raise unittest.SkipTest(f"MongoDB not reachable: {e}")

        # Setup test admin for admin endpoint comparison
        existing_admin = cls.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            cls.admin_id = existing_admin['_id']
        else:
            cls.admin_id = ObjectId()
            cls.db.users.insert_one({
                '_id': cls.admin_id,
                'name': 'Test Supreme Admin',
                'email': 'supreme_avail_test@admin.com',
                'role': 'supreme_admin'
            })

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def test_1_booking_html_contains_capacity_status_indicator(self):
        """Verify that user booking form contains the exact capacity status indicator matching admin form."""
        res = self.client.get('/book')
        self.assertEqual(res.status_code, 200)
        html = res.data.decode('utf-8')

        # Check presence of capacityInfoBox and availCapacityText
        self.assertIn('id="capacityInfoBox"', html)
        self.assertIn('id="availCapacityText"', html)
        self.assertIn('Available Capacity:', html)
        self.assertIn('Checking...', html)

        # Check that updateCapacityDisplay and updateSlotAvailability are wired in JS
        self.assertIn('updateCapacityDisplay', html)
        self.assertIn('updateSlotAvailability', html)
        self.assertIn('/slot_availability?day=', html)

    def test_2_slot_availability_endpoint_reports_correct_slots(self):
        """Verify /slot_availability endpoint returns accurate data for dates and slots."""
        test_date = (date.today() + timedelta(days=26)).isoformat()
        settings = load_settings(self.db)
        slots = settings.get('time_slots', ['7:00am', '10:00am', '1:00pm', '3:30pm'])

        # Clean any preexisting test data for this test date
        self.db.rafts.delete_many({'day': test_date})
        self.db.bookings.delete_many({'date': test_date})

        res = self.client.get(f'/slot_availability?day={test_date}')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        for s in slots:
            self.assertIn(s, data)
            # Empty slot bulk max capacity should be 35 (5 rafts * 7)
            self.assertEqual(data[s]['available'], 35)
            self.assertFalse(data[s]['full'])

    def test_3_admin_and_user_report_consistent_availability(self):
        """Verify admin pricing calculation and user slot_availability return identical capacities."""
        self._login_admin()
        test_date = (date.today() + timedelta(days=26)).isoformat()
        settings = load_settings(self.db)
        slot = settings.get('time_slots', ['7:00am'])[0]

        # Ensure fresh state
        self.db.rafts.delete_many({'day': test_date})
        self.db.bookings.delete_many({'date': test_date})

        # Query user endpoint
        res_user = self.client.get(f'/slot_availability?day={test_date}&_t=1')
        user_data = res_user.get_json()
        user_avail = user_data[slot]['available']

        # Query admin endpoint
        res_admin = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': test_date,
            'slot': slot,
            'group_size': 6
        })
        self.assertEqual(res_admin.status_code, 200)
        admin_data = res_admin.get_json()
        admin_avail = admin_data['available_capacity']

        self.assertEqual(user_avail, admin_avail)
        self.assertEqual(user_avail, 35)

    def test_4_existing_bookings_reduce_availability_consistently(self):
        """Verify confirmed bookings reduce available capacity identically for both user and admin."""
        self._login_admin()
        test_date = (date.today() + timedelta(days=26)).isoformat()
        settings = load_settings(self.db)
        slot = settings.get('time_slots', ['7:00am'])[0]

        self.db.rafts.delete_many({'day': test_date})
        self.db.bookings.delete_many({'date': test_date})

        # Book 10 people in that slot
        alloc = allocate_raft(self.db, None, test_date, slot, 10)
        self.assertEqual(alloc['status'], 'Confirmed')

        # Check user availability
        res_user = self.client.get(f'/slot_availability?day={test_date}&_t=2')
        user_data = res_user.get_json()
        user_avail = user_data[slot]['available']

        # Check admin availability
        res_admin = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': test_date,
            'slot': slot,
            'group_size': 5
        })
        self.assertEqual(res_admin.status_code, 200)
        admin_data = res_admin.get_json()
        admin_avail = admin_data['available_capacity']

        # Both should match
        self.assertEqual(user_avail, admin_avail)
        expected_eff = calculate_effective_available_capacity(self.db, test_date, slot)
        self.assertEqual(user_avail, expected_eff)

    def test_5_fully_booked_slot_displays_zero_availability(self):
        """Verify that when all rafts in a slot are completely full, available is 0 and full is True."""
        test_date = (date.today() + timedelta(days=26)).isoformat()
        settings = load_settings(self.db)
        slot = settings.get('time_slots', ['7:00am'])[0]

        self.db.rafts.delete_many({'day': test_date, 'slot': slot})

        # Mark all 5 rafts as full (occupancy 6)
        for i in range(1, 6):
            self.db.rafts.insert_one({
                'day': test_date,
                'slot': slot,
                'raft_id': i,
                'occupancy': 6,
                'is_special': False
            })

        res = self.client.get(f'/slot_availability?day={test_date}&_t=3')
        data = res.get_json()
        self.assertEqual(data[slot]['available'], 0)
        self.assertTrue(data[slot]['full'])

    def test_6_user_booking_submission_flow_unaffected(self):
        """Verify that normal customer booking submission still works and server validation checks capacity."""
        test_date = (date.today() + timedelta(days=26)).isoformat()
        settings = load_settings(self.db)
        slot = settings.get('time_slots', ['7:00am'])[0]

        # Reset slot to empty
        self.db.rafts.delete_many({'day': test_date, 'slot': slot})
        for i in range(1, 6):
            self.db.rafts.insert_one({
                'day': test_date,
                'slot': slot,
                'raft_id': i,
                'occupancy': 0,
                'is_special': False
            })

        post_data = {
            'name': 'Availability Test Customer',
            'email': 'availtest@gmail.com',
            'phone': '9876543210',
            'location': 'Cherupuzha',
            'booking_date': test_date,
            'slot': slot,
            'group_size': '6'
        }
        res = self.client.post('/book', data=post_data, follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/booking/', res.headers['Location'])
        self.assertIn('/confirmation', res.headers['Location'])

    @classmethod
    def tearDownClass(cls):
        # Clean up test date data
        test_date = (date.today() + timedelta(days=26)).isoformat()
        cls.db.rafts.delete_many({'day': test_date})
        cls.db.bookings.delete_many({'date': test_date})


if __name__ == '__main__':
    unittest.main()
