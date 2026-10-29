import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
import datetime
from app import create_app
from utils.allocation_logic import get_slots_for_date, load_settings

class TestImmediateSlots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.db = cls.app.mongo.db

        # Set up a supreme admin user
        cls.supreme_user = cls.db.users.find_one({'role': 'supreme_admin'})
        if not cls.supreme_user:
            cls.supreme_id = ObjectId()
            cls.supreme_user = {
                '_id': cls.supreme_id,
                'name': 'Supreme Admin Tester',
                'email': 'supreme_imm_test@raft.com',
                'role': 'supreme_admin'
            }
            cls.db.users.insert_one(cls.supreme_user)
        else:
            cls.supreme_id = cls.supreme_user['_id']

        # Set up a regular subadmin user
        cls.subadmin_user = cls.db.users.find_one({'role': 'subadmin'})
        if not cls.subadmin_user:
            cls.subadmin_id = ObjectId()
            cls.subadmin_user = {
                '_id': cls.subadmin_id,
                'name': 'Subadmin Tester',
                'email': 'subadmin_imm_test@raft.com',
                'role': 'subadmin'
            }
            cls.db.users.insert_one(cls.subadmin_user)
        else:
            cls.subadmin_id = cls.subadmin_user['_id']

        cls.test_date = '2026-10-28'
        cls.test_slot = '5:00pm'

    def setUp(self):
        self.client = self.app.test_client()
        # Clean up any leftover test data
        self.db.immediate_slots.delete_many({'date': self.test_date})
        self.db.rafts.delete_many({'day': self.test_date, 'slot': self.test_slot})
        self.db.bookings.delete_many({'date': self.test_date, 'slot': self.test_slot})

    def tearDown(self):
        self.db.immediate_slots.delete_many({'date': self.test_date})
        self.db.rafts.delete_many({'day': self.test_date, 'slot': self.test_slot})
        self.db.bookings.delete_many({'date': self.test_date, 'slot': self.test_slot})

    def _login_supreme(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.supreme_id)
            sess['_fresh'] = True

    def _login_subadmin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.subadmin_id)
            sess['_fresh'] = True

    def test_01_get_slots_for_date_logic(self):
        """Verify get_slots_for_date returns regular slots when no immediate slot, and merged slots when one exists."""
        settings = load_settings(self.db)
        standard_slots = list(settings.get('time_slots', []))
        
        # When no immediate slot exists
        slots_before = get_slots_for_date(self.db, self.test_date, settings)
        self.assertEqual(slots_before, standard_slots)
        self.assertNotIn(self.test_slot, slots_before)

        # Insert immediate slot
        self.db.immediate_slots.insert_one({
            'date': self.test_date,
            'slot': self.test_slot,
            'created_at': datetime.datetime.now(datetime.timezone.utc)
        })

        # When immediate slot exists
        slots_after = get_slots_for_date(self.db, self.test_date, settings)
        self.assertIn(self.test_slot, slots_after)
        self.assertEqual(len(slots_after), len(standard_slots) + 1)

    def test_02_supreme_admin_access_control(self):
        """Verify unauthorized or subadmin users are blocked from creating immediate slots."""
        # Unauthenticated request
        res_unauth = self.client.post('/admin/create_immediate_slot', json={'date': self.test_date, 'slot': self.test_slot})
        self.assertEqual(res_unauth.status_code, 302)  # redirects to login

        # Subadmin request
        self._login_subadmin()
        res_sub = self.client.post('/admin/create_immediate_slot', json={'date': self.test_date, 'slot': self.test_slot})
        self.assertEqual(res_sub.status_code, 302)  # redirects to dashboard

    def test_03_create_immediate_slot_validations(self):
        """Verify input validations for immediate slot creation."""
        self._login_supreme()

        # Missing date
        res = self.client.post('/admin/create_immediate_slot', json={'date': '', 'slot': self.test_slot})
        self.assertEqual(res.status_code, 400)
        self.assertIn('date is required', res.get_json()['error'].lower())

        # Missing slot
        res = self.client.post('/admin/create_immediate_slot', json={'date': self.test_date, 'slot': ''})
        self.assertEqual(res.status_code, 400)
        self.assertIn('slot name is required', res.get_json()['error'].lower())

        # Invalid date format
        res = self.client.post('/admin/create_immediate_slot', json={'date': 'invalid-date', 'slot': self.test_slot})
        self.assertEqual(res.status_code, 400)
        self.assertIn('invalid date format', res.get_json()['error'].lower())

        # Already a regular slot in settings
        settings = load_settings(self.db)
        if settings.get('time_slots'):
            reg_slot = settings['time_slots'][0]
            res = self.client.post('/admin/create_immediate_slot', json={'date': self.test_date, 'slot': reg_slot})
            self.assertEqual(res.status_code, 400)
            self.assertIn('already a regular daily time slot', res.get_json()['error'].lower())

    def test_04_create_and_list_immediate_slot_success(self):
        """Verify successful creation of immediate slot and listing via API."""
        self._login_supreme()

        # Create
        res = self.client.post('/admin/create_immediate_slot', json={'date': self.test_date, 'slot': self.test_slot})
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertTrue(json_data['success'])

        # Verify in DB
        db_record = self.db.immediate_slots.find_one({'date': self.test_date, 'slot': self.test_slot})
        self.assertIsNotNone(db_record)

        # Verify rafts were generated
        rafts = list(self.db.rafts.find({'day': self.test_date, 'slot': self.test_slot}))
        settings = load_settings(self.db)
        expected_rafts = settings.get('rafts_per_slot', 5)
        self.assertEqual(len(rafts), expected_rafts)

        # Duplicate rejection
        res_dup = self.client.post('/admin/create_immediate_slot', json={'date': self.test_date, 'slot': self.test_slot})
        self.assertEqual(res_dup.status_code, 400)
        self.assertIn('already exists', res_dup.get_json()['error'].lower())

        # List slots
        res_list = self.client.get('/admin/immediate_slots')
        self.assertEqual(res_list.status_code, 200)
        list_json = res_list.get_json()
        self.assertTrue(list_json['success'])
        slots_found = [s for s in list_json['slots'] if s['date'] == self.test_date and s['slot'] == self.test_slot]
        self.assertEqual(len(slots_found), 1)
        self.assertEqual(slots_found[0]['bookings_count'], 0)

    def test_05_slot_availability_and_booking_flow(self):
        """Verify slot_availability returns immediate slot and booking can be placed."""
        self._login_supreme()

        # Create immediate slot
        self.client.post('/admin/create_immediate_slot', json={'date': self.test_date, 'slot': self.test_slot})

        # Call slot_availability endpoint
        res_avail = self.client.get(f'/slot_availability?day={self.test_date}')
        self.assertEqual(res_avail.status_code, 200)
        avail_data = res_avail.get_json()
        self.assertIn(self.test_slot, avail_data)
        self.assertGreater(avail_data[self.test_slot]['available'], 0)
        self.assertFalse(avail_data[self.test_slot]['full'])

        # Create booking using the immediate slot
        booking_payload = {
            'name': 'Immediate Slot Tester',
            'email': 'imm_slot_test@gmail.com',
            'phone': '9876543210',
            'location': 'Cherupuzha',
            'booking_date': self.test_date,
            'slot': self.test_slot,
            'group_size': '4'
        }
        res_book = self.client.post('/book', data=booking_payload, follow_redirects=True)
        self.assertEqual(res_book.status_code, 200)

        # Verify booking in db
        b = self.db.bookings.find_one({'date': self.test_date, 'slot': self.test_slot, 'email': 'imm_slot_test@gmail.com'})
        self.assertIsNotNone(b)
        self.assertEqual(b['slot'], self.test_slot)

        # Verify immediate_slots listing now reflects 1 active booking
        res_list = self.client.get('/admin/immediate_slots')
        slots_found = [s for s in res_list.get_json()['slots'] if s['date'] == self.test_date and s['slot'] == self.test_slot]
        self.assertEqual(slots_found[0]['bookings_count'], 1)

        # Deletion must be blocked because active bookings exist
        slot_id = str(slots_found[0]['id'])
        res_del_blocked = self.client.post('/admin/delete_immediate_slot', json={'id': slot_id})
        self.assertEqual(res_del_blocked.status_code, 400)
        self.assertIn('active booking', res_del_blocked.get_json()['error'].lower())

        # Cancel the booking
        self.db.bookings.delete_one({'_id': b['_id']})

        # Deletion now succeeds
        res_del_success = self.client.post('/admin/delete_immediate_slot', json={'id': slot_id})
        self.assertEqual(res_del_success.status_code, 200)
        self.assertTrue(res_del_success.get_json()['success'])

        # Verify record removed
        self.assertIsNone(self.db.immediate_slots.find_one({'_id': ObjectId(slot_id)}))

if __name__ == '__main__':
    unittest.main()
