import os
import unittest
import json
import time
from datetime import date, timedelta
from bson.objectid import ObjectId
from app import create_app
from models.raft_model import ensure_rafts_for_date_slot
from utils.booking_ops import allocate_raft
from utils.effective_availability import calculate_effective_available_capacity

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

class TestAdminCapacityPricingIndependence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()
        cls.db = cls.app.mongo.db

        existing_admin = cls.db.users.find_one({"role": "admin"})
        if existing_admin:
            cls.admin_id = existing_admin['_id']
        else:
            cls.admin_id = ObjectId()
            cls.db.users.insert_one({
                '_id': cls.admin_id,
                'email': 'admin@rafting.test',
                'role': 'admin'
            })

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def test_1_template_structure_and_decoupling(self):
        """Verify admin_booking_form.html implements independent capacity display and pricing."""
        template_path = os.path.join(WORKSPACE_ROOT, 'templates', 'admin_booking_form.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. updateCapacityDisplay exists and reads from slotAvailMap
        self.assertIn("function updateCapacityDisplay()", content)
        self.assertIn("const selInfo = slotAvailMap[selectedSlot];", content)
        self.assertIn("const available = selInfo.available !== undefined ? selInfo.available : 0;", content)
        self.assertIn("const maxSmallGroup = selInfo.max_small_group !== undefined ? selInfo.max_small_group : 0;", content)
        self.assertIn("const hasSmallGroupCap = wanted > 0 && wanted < 4 ? (wanted <= maxSmallGroup) : (selInfo.has_small_group_capacity || false);", content)

        # 2. updateSlotAvailability fetches /slot_availability and uses renderSlotOptionsAndCapacity
        self.assertIn("fetch('/slot_availability?day=' + encodeURIComponent(day)", content)
        self.assertIn("function renderSlotOptionsAndCapacity(", content)
        self.assertIn("renderSlotOptionsAndCapacity(slotSelect, groupSizeInput);", content)

        # 3. Request sequencing safeguards
        self.assertIn("availReqId", content)
        self.assertIn("if (reqId !== availReqId) return;", content)
        self.assertIn("pricingReqId", content)
        self.assertIn("if (currentReqId !== pricingReqId) return;", content)

        # 4. Same-date optimization check exists
        self.assertIn("lastFetchedDate", content)
        self.assertIn("if (day === lastFetchedDate && slotAvailMap && Object.keys(slotAvailMap).length > 0)", content)

        # 5. Immediate UI clearing on date change
        self.assertIn("resetAvailabilityUI();", content)

        # 6. Event listeners trigger updateCapacityDisplay
        self.assertIn("slotSelect.addEventListener('change', function ()", content)
        self.assertIn("updateCapacityDisplay();", content)
        self.assertIn("slotSelect.addEventListener('input', function ()", content)
        self.assertIn("groupSizeInput.addEventListener('input', function ()", content)

        # 7. recalculatePricing does NOT overwrite capacity elements
        recalc_start = content.find("function recalculatePricing()")
        self.assertNotEqual(recalc_start, -1)
        recalc_end = content.find("document.addEventListener('DOMContentLoaded'", recalc_start)
        recalc_body = content[recalc_start:recalc_end]

        self.assertNotIn("capText.innerText = data.available_capacity", recalc_body)
        self.assertNotIn("capText.innerText = '0 seats (Full!)'", recalc_body)
        self.assertNotIn("data.available_capacity <= 0", recalc_body)
        self.assertNotIn("wanted > data.available_capacity", recalc_body)

    def test_2_slot_availability_endpoint_data_contract(self):
        """Verify /slot_availability provides all fields needed by updateCapacityDisplay."""
        test_date = (date.today() + timedelta(days=22)).isoformat()
        slot = '7:00am'

        self.db.rafts.delete_many({'day': test_date})
        self.db.bookings.delete_many({'date': test_date})

        res = self.client.get(f'/slot_availability?day={test_date}&_t={int(time.time()*1000)}')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertIn(slot, data)
        slot_info = data[slot]
        self.assertIn('available', slot_info)
        self.assertIn('full', slot_info)
        self.assertIn('has_small_group_capacity', slot_info)
        self.assertIn('max_small_group', slot_info)

        # Empty slot: 35 seats available, full is False
        self.assertEqual(slot_info['available'], 35)
        self.assertFalse(slot_info['full'])

    def test_3_pricing_endpoint_independence_and_calculations(self):
        """Verify /admin/calculate_admin_booking_pricing returns accurate pricing data."""
        self._login_admin()
        test_date = (date.today() + timedelta(days=22)).isoformat()
        slot = '7:00am'

        payload = {
            'date': test_date,
            'slot': slot,
            'group_size': 5,
            'per_person_discount': '100',
            'custom_advance': '1500',
            'coupon_code': ''
        }
        res = self.client.post('/admin/calculate_admin_booking_pricing', json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertTrue(data['success'])
        self.assertIn('total_amount', data)
        self.assertIn('final_advance', data)
        self.assertIn('balance_amount', data)
        self.assertEqual(data['per_person_discount'], 100.0)
        self.assertEqual(data['final_advance'], 1500.0)
        self.assertEqual(data['balance_amount'], round(data['total_amount'] - 1500.0, 2))

    def test_4_full_and_partial_capacity_reflects_accurately(self):
        """Verify that partially occupied and full rafts report accurate capacity in slot_availability."""
        test_date = (date.today() + timedelta(days=23)).isoformat()
        slot = '7:00am'

        self.db.rafts.delete_many({'day': test_date})
        self.db.bookings.delete_many({'date': test_date})

        # Partial booking: 4 people
        alloc = allocate_raft(self.db, None, test_date, slot, 4)
        self.assertEqual(alloc['status'], 'Confirmed')

        res = self.client.get(f'/slot_availability?day={test_date}&_t={int(time.time()*1000)}')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        slot_info = data[slot]

        expected_eff = calculate_effective_available_capacity(self.db, test_date, slot)
        self.assertEqual(slot_info['available'], expected_eff)
        self.assertFalse(slot_info['full'])
        self.assertTrue(slot_info['has_small_group_capacity'])
        self.assertEqual(slot_info['max_small_group'], 2)

        # Fully book remaining
        self.db.rafts.update_many(
            {'day': test_date, 'slot': slot},
            {'$set': {'occupancy': 6, 'status': 'Occupied', 'is_special': False}}
        )

        res_full = self.client.get(f'/slot_availability?day={test_date}&_t={int(time.time()*1000) + 1}')
        self.assertEqual(res_full.status_code, 200)
        data_full = res_full.get_json()
        slot_full_info = data_full[slot]

        self.assertEqual(slot_full_info['available'], 0)
        self.assertTrue(slot_full_info['full'])

        # Cleanup
        self.db.rafts.delete_many({'day': test_date})
        self.db.bookings.delete_many({'date': test_date})

    def test_5_booking_creation_still_validates_capacity_authoritatively(self):
        """Verify server-side capacity validation still prevents overbooking on submission."""
        self._login_admin()
        test_date = (date.today() + timedelta(days=24)).isoformat()
        slot = '10:00am'

        self.db.rafts.delete_many({'day': test_date})
        self.db.bookings.delete_many({'date': test_date})

        # Mark all rafts full
        ensure_rafts_for_date_slot(self.db, test_date, slot, 5, 6)
        self.db.rafts.update_many(
            {'day': test_date, 'slot': slot},
            {'$set': {'occupancy': 6, 'status': 'Occupied', 'is_special': False}}
        )

        post_data = {
            'name': 'Overbook Test',
            'phone': '9876543299',
            'email': 'overbook@gmail.com',
            'location': 'Kannur',
            'booking_date': test_date,
            'slot': slot,
            'group_size': '4',
            'per_person_discount': '0',
            'custom_advance_amount': '0',
            'payment_mode': 'Cash'
        }
        res = self.client.post('/admin/booking/create', data=post_data, follow_redirects=True)
        html = res.get_data(as_text=True)

        # Form must reject over-capacity booking
        self.assertIn('Selected slot is fully booked', html)

        # Cleanup
        self.db.rafts.delete_many({'day': test_date})
        self.db.bookings.delete_many({'date': test_date})

if __name__ == '__main__':
    unittest.main()
