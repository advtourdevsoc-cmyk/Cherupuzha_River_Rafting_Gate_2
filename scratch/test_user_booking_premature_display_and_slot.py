import os
import sys
import unittest
from datetime import date, timedelta
from bson import ObjectId

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from app import create_app
from utils.allocation_logic import load_settings
from models.raft_model import ensure_rafts_for_date_slot

class TestUserBookingPrematureDisplayAndSlot(unittest.TestCase):
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

    def tearDown(self):
        self.db.bookings.delete_many({'email': 'testuserpremature@gmail.com'})

    def test_1_initial_page_load_shows_placeholder_and_hidden_availability(self):
        """1. Opening the user booking page shows 'Select a time slot' and no availability information."""
        res = self.client.get('/book')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Dropdown contains disabled selected 'Select a time slot'
        self.assertIn('<option value="" disabled selected>Select a time slot</option>', html)

        # Capacity box has the 'hidden' class initially
        self.assertIn('id="capacityInfoBox"', html)
        self.assertIn('id="capacityInfoBox" class="p-3 rounded-lg text-xs font-semibold bg-teal-50 text-teal-800 border border-teal-200 flex justify-between items-center mb-3 hidden"', html)

        # Verify that DOMContentLoaded does NOT call updateSlotAvailability with minDate when dateInput.value is empty
        self.assertIn("if (dateInput && dateInput.value && dateInput.value.trim() !== '') {", html)
        self.assertIn("updateSlotAvailability(dateInput.value.trim());", html)
        self.assertIn("} else {", html)
        self.assertIn("resetAvailabilityUI();", html)

    def test_2_valid_time_slots_order_preserved(self):
        """3. Clicking the time-slot dropdown reveals the existing valid time slots in correct order."""
        res = self.client.get('/book')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        slot_part = html.split('id="slot-select"')[1].split('</select>')[0]
        last_pos = -1
        for s in self.time_slots:
            opt = f'<option value="{s}">{s}</option>'
            self.assertIn(opt, slot_part)
            pos = slot_part.find(opt)
            self.assertGreater(pos, last_pos, f"Slot {s} out of order")
            last_pos = pos

    def test_3_client_and_server_side_guard_logic_in_template(self):
        """2, 6, 7, 8: Verify client-side availability guard, clearing logic, race-condition handling, and error handling."""
        template_path = os.path.join(WORKSPACE_ROOT, 'templates', 'booking.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Guard: requires both valid date and valid time slot
        self.assertIn("const selectedDate = (dateInput ? dateInput.value : '').trim();", content)
        self.assertIn("const selectedSlot = (slotSelect ? slotSelect.value : '').trim();", content)
        self.assertIn("if (!selectedDate || !selectedSlot || selectedSlot.toLowerCase().startsWith('select')) {", content)
        self.assertIn("resetAvailabilityUI();", content)

        # Reset availability UI function hides capacity box
        self.assertIn("function resetAvailabilityUI() {", content)
        self.assertIn("capBox.className = \"p-3 rounded-lg text-xs font-semibold bg-teal-50 text-teal-800 border border-teal-200 flex justify-between items-center mb-3 hidden\";", content)

        # Race condition protection using request sequence id
        self.assertIn("let availReqId = 0;", content)
        self.assertIn("const reqId = ++availReqId;", content)
        self.assertIn("if (reqId !== availReqId) return;", content)

        # Clearing date triggers updateSlotAvailability(null)
        self.assertIn("if (!dateStr) {", content)
        self.assertIn("updateSlotAvailability(null);", content)

        # Availability API failure does not show 0 capacity or Full message
        self.assertIn("capText.innerText = 'Unable to check availability';", content)
        self.assertNotIn("capText.innerText = '0 seats (Full!)';\n          return;", content)

        # Submit handler prevents submitting without date or slot
        self.assertIn("if (!slotVal || slotVal.toLowerCase().startsWith('select')) {", content)
        self.assertIn("if (!dateVal) {", content)

    def test_4_server_rejects_empty_or_placeholder_slot(self):
        """9. Submitting without a valid date or time slot is rejected by server-side validation."""
        test_date = (date.today() + timedelta(days=5)).isoformat()

        # A: Empty slot
        post_data_empty = {
            'name': 'Test User',
            'email': 'testuserpremature@gmail.com',
            'phone': '9876543210',
            'location': 'Cherupuzha',
            'booking_date': test_date,
            'slot': '',
            'group_size': '4'
        }
        res_empty = self.client.post('/book', data=post_data_empty, follow_redirects=True)
        self.assertEqual(res_empty.status_code, 200)
        self.assertIn('Please select a valid time slot.', res_empty.get_data(as_text=True))
        self.assertIsNone(self.db.bookings.find_one({'email': 'testuserpremature@gmail.com'}))

        # B: Placeholder slot text
        post_data_placeholder = {
            'name': 'Test User',
            'email': 'testuserpremature@gmail.com',
            'phone': '9876543210',
            'location': 'Cherupuzha',
            'booking_date': test_date,
            'slot': 'Select a time slot',
            'group_size': '4'
        }
        res_placeholder = self.client.post('/book', data=post_data_placeholder, follow_redirects=True)
        self.assertEqual(res_placeholder.status_code, 200)
        self.assertIn('Please select a valid time slot.', res_placeholder.get_data(as_text=True))
        self.assertIsNone(self.db.bookings.find_one({'email': 'testuserpremature@gmail.com'}))

        # C: Empty booking date
        post_data_nodate = {
            'name': 'Test User',
            'email': 'testuserpremature@gmail.com',
            'phone': '9876543210',
            'location': 'Cherupuzha',
            'booking_date': '',
            'slot': '10:00am',
            'group_size': '4'
        }
        res_nodate = self.client.post('/book', data=post_data_nodate, follow_redirects=True)
        self.assertEqual(res_nodate.status_code, 200)
        self.assertIn('Please provide a booking date.', res_nodate.get_data(as_text=True))
        self.assertIsNone(self.db.bookings.find_one({'email': 'testuserpremature@gmail.com'}))

    def test_5_genuine_booking_submission_succeeds(self):
        """10. Submitting with valid date and time slot succeeds and redirects to confirmation."""
        test_date = (date.today() + timedelta(days=5)).isoformat()
        valid_slot = '10:00am'

        # Ensure rafts exist for this date and slot
        ensure_rafts_for_date_slot(self.db, test_date, valid_slot, 5, 6)
        self.db.rafts.update_many(
            {'day': test_date, 'slot': valid_slot},
            {'$set': {'occupancy': 0, 'status': 'Active'}}
        )

        post_data = {
            'name': 'Test User',
            'email': 'testuserpremature@gmail.com',
            'phone': '9876543210',
            'location': 'Cherupuzha',
            'booking_date': test_date,
            'slot': valid_slot,
            'group_size': '4'
        }
        res = self.client.post('/book', data=post_data, follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn('/booking/', res.headers['Location'])
        self.assertIn('/confirmation', res.headers['Location'])

        booking = self.db.bookings.find_one({'email': 'testuserpremature@gmail.com'})
        self.assertIsNotNone(booking)
        self.assertEqual(booking['booking_details']['date'], test_date)
        self.assertEqual(booking['booking_details']['slot'], valid_slot)
        self.assertEqual(booking['booking_details']['group_size'], 4)

if __name__ == '__main__':
    unittest.main()
