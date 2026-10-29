import os
import unittest
from datetime import date, timedelta
from bson import ObjectId
from app import create_app
from config import Config

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class TestAdminBookingPrematureMessages(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.db = self.app.mongo.db

        # Configure system settings
        self.settings = {
            '_id': 'system_settings',
            'days': 30,
            'slots': 4,
            'rafts_per_slot': 5,
            'capacity': 6,
            'weekday_amount': 500,
            'saturday_amount': 700,
            'time_slots': ['7:00am', '10:00am', '1:00pm', '3:30pm'],
            'platform_fee': 0,
            'normal_max_people_per_slot': 30,
            'bulk_max_people_per_slot': 30
        }
        self.db.settings.replace_one({'_id': 'system_settings'}, self.settings, upsert=True)

        # Ensure test rafts exist
        if self.db.rafts.count_documents({}) == 0:
            for i in range(1, 6):
                self.db.rafts.insert_one({
                    'raft_number': f'R{i}',
                    'capacity': 6,
                    'status': 'Active'
                })

        # Find or create supreme admin user
        existing_admin = self.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            self.admin_id = existing_admin['_id']
            self.admin_doc = existing_admin
        else:
            self.admin_id = ObjectId()
            self.admin_doc = {
                '_id': self.admin_id,
                'email': 'admin_test_avail@gmail.com',
                'role': 'supreme_admin',
                'name': 'Test Admin Avail'
            }
            self.db.users.insert_one(self.admin_doc)

    def tearDown(self):
        self.db.bookings.delete_many({'user_id': 'admin_test_avail@gmail.com'})
        self.db.bookings.delete_many({'phone': '9876543210'})

    def test_scenario_1_initial_get_page_has_no_premature_messages(self):
        """Scenario 1: Opening the admin booking form without entering anything displays neither message."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/booking/create')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # 1. capacityInfoBox must have hidden class initially
        self.assertIn('id="capacityInfoBox"', html)
        self.assertIn('id="capacityInfoBox" class="p-3 rounded-lg text-xs font-semibold bg-teal-50 text-teal-800 border border-teal-200 flex justify-between items-center hidden"', html)

        # 2. Fully booked messages must NOT be present in DOM elements
        error_banner_html = html.split('id="booking-error-banner"')[1].split('</div>')[0]
        self.assertNotIn('Selected slot is fully booked', error_banner_html)

        cap_box_html = html.split('id="capacityInfoBox"')[1].split('</div>')[0]
        self.assertNotIn('0 seats (Full!)', cap_box_html)

        # 3. Time slot dropdown must have empty placeholder selected and disabled
        self.assertIn('<option value="" disabled selected>Select the time slot</option>', html)

        # 4. Error banner must be hidden
        self.assertIn('id="booking-error-banner"', html)
        self.assertIn('hidden', html.split('id="booking-error-banner"')[1].split('>')[0])

    def test_scenario_2_backend_endpoint_guards_empty_selections(self):
        """Scenario 2 & 7: Empty date or slot returns available_capacity as None, not 0."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # Case A: Both date and slot empty
        res1 = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': '',
            'slot': '',
            'group_size': 4
        })
        self.assertEqual(res1.status_code, 200)
        data1 = res1.get_json()
        self.assertTrue(data1['success'])
        self.assertIsNone(data1['available_capacity'], "available_capacity must be None when date and slot are missing")

        # Case B: Date provided, slot empty
        test_date = (date.today() + timedelta(days=5)).isoformat()
        res2 = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': test_date,
            'slot': '',
            'group_size': 4
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertTrue(data2['success'])
        self.assertIsNone(data2['available_capacity'], "available_capacity must be None when slot is missing")

        # Case C: Slot provided, date empty
        res3 = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': '',
            'slot': '7:00am',
            'group_size': 4
        })
        self.assertEqual(res3.status_code, 200)
        data3 = res3.get_json()
        self.assertTrue(data3['success'])
        self.assertIsNone(data3['available_capacity'], "available_capacity must be None when date is missing")

    def test_scenario_3_valid_date_and_available_slot(self):
        """Scenario 3: Selecting a valid date and available time slot returns correct capacity."""
        test_date = (date.today() + timedelta(days=5)).isoformat()
        test_slot = '10:00am'

        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': test_date,
            'slot': test_slot,
            'group_size': 4
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertIsNotNone(data['available_capacity'])
        self.assertGreater(data['available_capacity'], 0, "Available capacity should be > 0 for empty slot")

    def test_scenario_4_and_5_full_slot_and_switching_to_available(self):
        """Scenario 4 & 5: Genuinely full slot returns 0 capacity, and available slot returns > 0 capacity."""
        from models.raft_model import ensure_rafts_for_date_slot
        test_date = (date.today() + timedelta(days=6)).isoformat()
        full_slot = '7:00am'
        avail_slot = '10:00am'

        # Ensure and fill rafts for full_slot
        ensure_rafts_for_date_slot(self.db, test_date, full_slot, 5, 6)
        self.db.rafts.update_many(
            {'day': test_date, 'slot': full_slot},
            {'$set': {'occupancy': 6, 'status': 'Occupied'}}
        )

        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # Check full slot
        res_full = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': test_date,
            'slot': full_slot,
            'group_size': 4
        })
        self.assertEqual(res_full.status_code, 200)
        data_full = res_full.get_json()
        self.assertEqual(data_full['available_capacity'], 0, "Full slot must report 0 available capacity")

        # Check available slot on same date
        res_avail = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': test_date,
            'slot': avail_slot,
            'group_size': 4
        })
        self.assertEqual(res_avail.status_code, 200)
        data_avail = res_avail.get_json()
        self.assertGreater(data_avail['available_capacity'], 0, "Available slot must report > 0 capacity")

        # Cleanup
        self.db.rafts.delete_many({'day': test_date})

    def test_scenario_6_template_guard_and_clear_logic(self):
        """Scenario 6, 7 & 8: Verify template guards against empty/cleared selections and race conditions."""
        template_path = os.path.join(WORKSPACE_ROOT, 'templates', 'admin_booking_form.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Check guard in recalculatePricing
        self.assertIn("const selectedDate = (dateInput.value || '').trim();", content)
        self.assertIn("const selectedSlot = (slotSelect.value || '').trim();", content)
        self.assertIn("if (!selectedDate || !selectedSlot || selectedSlot.toLowerCase().startsWith('select')) {", content)
        self.assertIn("resetAvailabilityUI();", content)

        # Check resetAvailabilityUI hides capacity box and banner
        self.assertIn("capBox.className = \"p-3 rounded-lg text-xs font-semibold bg-teal-50 text-teal-800 border border-teal-200 flex justify-between items-center hidden\";", content)
        self.assertIn("errorBanner.classList.add('hidden');", content)

        # Check rapid selection change guard
        self.assertIn("if (currentReqId !== pricingReqId) return;", content)
        self.assertIn("if (!curDate || !curSlot || curDate !== selectedDate || curSlot !== selectedSlot)", content)

        # Check fetch catch error message is neutral, not 0 seats (Full!)
        self.assertIn("capText.innerText = 'Unable to check availability';", content)
        self.assertNotIn("capText.innerText = '0 seats (Full!)';\n    });", content)

    def test_scenario_9_booking_creation_and_validation(self):
        """Scenario 9: Server-side capacity validation and raft allocation continue to work correctly."""
        test_date = (date.today() + timedelta(days=7)).isoformat()
        test_slot = '10:00am'

        self.db.bookings.delete_many({'date': test_date})

        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # Valid booking creation
        post_data = {
            'name': 'Valid Customer',
            'phone': '9876543210',
            'email': 'validtester@gmail.com',
            'location': 'Kannur',
            'booking_date': test_date,
            'slot': test_slot,
            'group_size': '4',
            'per_person_discount': '50',
            'custom_advance_amount': '500',
            'payment_mode': 'Cash'
        }
        res = self.client.post('/admin/booking/create', data=post_data, follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertTrue(res.headers['Location'].endswith('/admin/booking'))

        created = self.db.bookings.find_one({'date': test_date, 'slot': test_slot, 'phone': '9876543210'})
        self.assertIsNotNone(created)
        self.assertEqual(created['status'], 'Confirmed')
        self.assertTrue(len(created['raft_allocations']) > 0)

        # Cleanup
        self.db.bookings.delete_many({'date': test_date})


if __name__ == '__main__':
    unittest.main()
