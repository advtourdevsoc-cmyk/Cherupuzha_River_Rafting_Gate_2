import os
import sys
import unittest
from datetime import date, timedelta
from bson import ObjectId

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from app import create_app

class TestAdminBookingSlotDropdown(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.db = self.app.mongo.db

        self.time_slots = ['7:00am', '10:00am', '1:00pm', '3:30pm']
        self.settings = {
            '_id': 'system_settings',
            'days': 30,
            'slots': 4,
            'rafts_per_slot': 5,
            'capacity': 6,
            'weekday_amount': 500,
            'saturday_amount': 700,
            'time_slots': self.time_slots,
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

        # Ensure supreme admin user
        existing_admin = self.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            self.admin_id = existing_admin['_id']
        else:
            self.admin_id = ObjectId()
            self.db.users.insert_one({
                '_id': self.admin_id,
                'email': 'admin_slot_test@gmail.com',
                'role': 'supreme_admin',
                'name': 'Test Slot Admin'
            })

    def tearDown(self):
        self.db.bookings.delete_many({'phone': '9876543211'})

    def test_1_dropdown_html_structure_and_order(self):
        """Verify dropdown markup has 'Select the time slot' placeholder, value='', disabled, and preserved slots in order."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/booking/create')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # 1. Placeholder is disabled, empty value, and text is 'Select the time slot'
        self.assertIn('<option value="" disabled selected>Select the time slot</option>', html)

        # 2. All genuine time slots are preserved in exact order
        slot_select_part = html.split('id="slot-select"')[1].split('</select>')[0]
        last_pos = -1
        for slot in self.time_slots:
            opt_tag = f'<option value="{slot}"'
            self.assertIn(opt_tag, slot_select_part)
            pos = slot_select_part.find(opt_tag)
            self.assertGreater(pos, last_pos, f"Slot {slot} out of order")
            last_pos = pos

    def test_2_backend_rejects_empty_slot(self):
        """Verify backend rejects submission when slot is empty."""
        test_date = (date.today() + timedelta(days=5)).isoformat()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        post_data = {
            'name': 'Test Customer',
            'phone': '9876543211',
            'email': 'customer@gmail.com',
            'location': 'Kannur',
            'booking_date': test_date,
            'slot': '',
            'group_size': '4',
            'payment_mode': 'Cash'
        }
        res = self.client.post('/admin/booking/create', data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('Please select a valid time slot.', html)

        # Ensure no booking was created
        created = self.db.bookings.find_one({'phone': '9876543211'})
        self.assertIsNone(created)

    def test_3_backend_rejects_placeholder_slot_value(self):
        """Verify backend rejects submission when slot is 'Select the time slot'."""
        test_date = (date.today() + timedelta(days=5)).isoformat()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        post_data = {
            'name': 'Test Customer',
            'phone': '9876543211',
            'email': 'customer@gmail.com',
            'location': 'Kannur',
            'booking_date': test_date,
            'slot': 'Select the time slot',
            'group_size': '4',
            'payment_mode': 'Cash'
        }
        res = self.client.post('/admin/booking/create', data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('Please select a valid time slot.', html)

        # Ensure no booking was created
        created = self.db.bookings.find_one({'phone': '9876543211'})
        self.assertIsNone(created)

    def test_4_backend_pricing_endpoint_ignores_placeholder_slot(self):
        """Verify calculate_admin_booking_pricing returns available_capacity=None when slot is empty or placeholder."""
        test_date = (date.today() + timedelta(days=5)).isoformat()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # Empty slot
        res_empty = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': test_date,
            'slot': '',
            'group_size': '4'
        })
        self.assertEqual(res_empty.status_code, 200)
        data_empty = res_empty.get_json()
        self.assertIsNone(data_empty.get('available_capacity'))

        # Placeholder text slot
        res_placeholder = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': test_date,
            'slot': 'Select the time slot',
            'group_size': '4'
        })
        self.assertEqual(res_placeholder.status_code, 200)
        data_placeholder = res_placeholder.get_json()
        self.assertIsNone(data_placeholder.get('available_capacity'))

    def test_5_genuine_slot_booking_succeeds(self):
        """Verify selecting a genuine time slot creates the booking and allocates rafts."""
        test_date = (date.today() + timedelta(days=5)).isoformat()
        valid_slot = self.time_slots[1] # '10:00am'

        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        post_data = {
            'name': 'Valid Customer',
            'phone': '9876543211',
            'email': 'validtester@gmail.com',
            'location': 'Kannur',
            'booking_date': test_date,
            'slot': valid_slot,
            'group_size': '4',
            'per_person_discount': '0',
            'custom_advance_amount': '0',
            'payment_mode': 'Cash'
        }
        res = self.client.post('/admin/booking/create', data=post_data, follow_redirects=False)
        self.assertEqual(res.status_code, 302)

        created = self.db.bookings.find_one({'phone': '9876543211'})
        self.assertIsNotNone(created)
        self.assertEqual(created['slot'], valid_slot)
        self.assertEqual(created['status'], 'Confirmed')

    def test_6_frontend_template_js_validation_elements(self):
        """Verify admin_booking_form.html contains JS validation preventing submit with empty/placeholder slot."""
        template_path = os.path.join(WORKSPACE_ROOT, 'templates', 'admin_booking_form.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Submit handler slot check
        self.assertIn("const slotSelect = document.getElementById('slot-select');", content)
        self.assertIn("const slotVal = (slotSelect ? slotSelect.value : '').trim();", content)
        self.assertIn("if (!slotVal || slotVal.toLowerCase().startsWith('select')) {", content)
        self.assertIn("errorBanner.innerHTML = '<p>❌ Please select a valid time slot.</p>';", content)

        # recalculatePricing guard
        self.assertIn("if (!selectedDate || !selectedSlot || selectedSlot.toLowerCase().startsWith('select')) {", content)

if __name__ == '__main__':
    unittest.main()
