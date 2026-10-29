import sys, os
sys.path.insert(0, os.path.abspath('.'))

import unittest
from bson import ObjectId
from app import create_app
from utils.booking_ops import cancel_booking, recompute_occupancy_for_slot, check_capacity_available
from utils.effective_availability import calculate_effective_available_capacity
from utils.allocation_logic import allocate_raft
from models.raft_model import ensure_rafts_for_date_slot

class TestRebookAndOccupancySync(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.db = self.app.mongo.db
        self.test_date = '2026-10-25'
        
        # Get admin user ID
        admin = self.db.users.find_one({'role': 'admin'})
        self.admin_id = str(admin['_id']) if admin else '6ac86ede1750a86c3551d5f8'

        # Clean up any test data for test_date
        self.db.bookings.delete_many({'date': self.test_date})
        self.db.rafts.delete_many({'day': self.test_date})

    def tearDown(self):
        self.db.bookings.delete_many({'date': self.test_date})
        self.db.rafts.delete_many({'day': self.test_date})

    def test_rebook_pending_frees_old_slot_and_allocates_new_slot(self):
        # 1. Simulate a pending booking for 25 people at 10:00am that had raft allocations
        ensure_rafts_for_date_slot(self.db, self.test_date, '10:00am', 5, 6)
        alloc_res = allocate_raft(self.db, None, self.test_date, '10:00am', 25)
        self.assertEqual(alloc_res['status'], 'Confirmed')

        # Create booking document in Pending state with the provisional allocations
        b_id = self.db.bookings.insert_one({
            'date': self.test_date,
            'slot': '10:00am',
            'group_size': 25,
            'status': 'Pending',
            'payment_status': 'Paid',
            'allocation_error': True,
            'raft_allocations': alloc_res['rafts'],
            'raft_allocation_details': alloc_res['raft_details'],
            'amount': 32500.0,
            'total_amount': 32500.0,
            'advance_amount': 8125.0,
            'balance_amount': 24375.0,
            'phone': '9999999999',
            'email': 'test@example.com',
            'user_name': 'Test User',
            'booking_details': {
                'date': self.test_date,
                'slot': '10:00am',
                'group_size': 25
            }
        }).inserted_id

        # Before rebook: 10:00am has 25 occupied in rafts
        cap_10_before = calculate_effective_available_capacity(self.db, self.test_date, '10:00am')
        self.assertEqual(cap_10_before, 7) # only raft 5 is empty (capacity 7)

        # 2. Login as admin via session_transaction and call /admin/rebook_pending/<id>
        with self.client.session_transaction() as sess:
            sess['_user_id'] = self.admin_id
            sess['_fresh'] = True

        res = self.client.post(
            f'/admin/rebook_pending/{b_id}',
            json={'new_date': self.test_date, 'new_slot': '3:30pm'}
        )
        self.assertEqual(res.status_code, 200, f"Rebook failed: {res.get_json()}")
        
        # 3. Verify old slot (10:00am) is completely freed!
        cap_10_after = calculate_effective_available_capacity(self.db, self.test_date, '10:00am')
        self.assertEqual(cap_10_after, 35, "10:00am should have full bulk capacity (35) after rebooking!")

        rafts_10 = list(self.db.rafts.find({'day': self.test_date, 'slot': '10:00am'}))
        for r in rafts_10:
            self.assertEqual(r['occupancy'], 0, f"Raft {r['raft_id']} in 10:00am should have 0 occupancy")

        # 4. Verify new slot (3:30pm) has the 25 people properly allocated across rafts 1, 2, 3, 4
        rafts_330 = list(self.db.rafts.find({'day': self.test_date, 'slot': '3:30pm'}).sort('raft_id', 1))
        self.assertEqual(len(rafts_330), 5)
        self.assertEqual([r['occupancy'] for r in rafts_330], [6, 6, 6, 7, 0])
        self.assertFalse(rafts_330[0]['is_special'])
        self.assertTrue(rafts_330[3]['is_special'])

        # 5. Verify booking document updated properly
        updated_b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(updated_b['status'], 'Confirmed')
        self.assertEqual(updated_b['payment_status'], 'Paid')
        self.assertEqual(updated_b['slot'], '3:30pm')
        self.assertEqual(updated_b['raft_allocations'], [1, 2, 3, 4])
        self.assertFalse(updated_b.get('allocation_error', False))

    def test_ensure_rafts_handles_concurrent_initialization(self):
        # Call ensure_rafts_for_date_slot multiple times consecutively
        ensure_rafts_for_date_slot(self.db, self.test_date, '1:00pm', 5, 6)
        ensure_rafts_for_date_slot(self.db, self.test_date, '1:00pm', 5, 6)
        
        rafts = list(self.db.rafts.find({'day': self.test_date, 'slot': '1:00pm'}))
        self.assertEqual(len(rafts), 5, "Should have exactly 5 rafts without duplicates")
        raft_ids = [r['raft_id'] for r in rafts]
        self.assertEqual(sorted(raft_ids), [1, 2, 3, 4, 5])

    def test_cancel_pending_booking_with_allocations_frees_rafts(self):
        # Create pending booking with raft allocations
        ensure_rafts_for_date_slot(self.db, self.test_date, '7:00am', 5, 6)
        alloc_res = allocate_raft(self.db, None, self.test_date, '7:00am', 12)

        b_id = self.db.bookings.insert_one({
            'date': self.test_date,
            'slot': '7:00am',
            'group_size': 12,
            'status': 'Pending',
            'raft_allocations': alloc_res['rafts'],
            'raft_allocation_details': alloc_res['raft_details'],
        }).inserted_id

        # Cancel it
        cancel_res = cancel_booking(self.db, b_id)
        self.assertIn('message', cancel_res)

        # Verify capacity is freed
        cap_7 = calculate_effective_available_capacity(self.db, self.test_date, '7:00am')
        self.assertEqual(cap_7, 35)

    def test_rebook_pending_preserves_advance_balance_and_coupon(self):
        # Create a pending booking with coupon discount and custom advance
        ensure_rafts_for_date_slot(self.db, self.test_date, '7:00am', 5, 6)
        ensure_rafts_for_date_slot(self.db, self.test_date, '10:00am', 5, 6)
        
        b_id = self.db.bookings.insert_one({
            'date': self.test_date,
            'slot': '7:00am',
            'group_size': 7,
            'status': 'Pending',
            'payment_status': 'Paid',
            'amount': 7042.0,
            'total_amount': 7042.0,
            'advance_amount': 1750.0,
            'balance_amount': 5292.0,
            'coupon_discount_applied': 2100.0,
            'coupon_code': 'RAFT-TEST',
            'price_amount': 7000.0,
            'platform_fee': 42.0,
            'original_total_amount': 9154.6,
            'original_advance_amount': 2275.0,
            'phone': '8590821155',
            'email': 'johnpaulalex85@gmail.com',
            'user_name': 'John Paul Alex',
            'booking_details': {
                'date': self.test_date,
                'slot': '7:00am',
                'group_size': 7,
                'advance_amount': 1750.0,
                'balance_amount': 5292.0,
                'total_amount': 7042.0,
                'coupon_discount_applied': 2100.0,
                'coupon_code': 'RAFT-TEST'
            }
        }).inserted_id

        # Admin calls rebook_pending
        with self.client.session_transaction() as sess:
            sess['_user_id'] = self.admin_id
            sess['_fresh'] = True

        res = self.client.post(
            f'/admin/rebook_pending/{b_id}',
            json={'new_date': self.test_date, 'new_slot': '10:00am'}
        )
        self.assertEqual(res.status_code, 200, f"Rebook failed: {res.get_json()}")

        # Verify booking financial amounts are strictly preserved
        updated_b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(updated_b['status'], 'Confirmed')
        self.assertEqual(updated_b['date'], self.test_date)
        self.assertEqual(updated_b['slot'], '10:00am')
        self.assertEqual(updated_b['advance_amount'], 1750.0)
        self.assertEqual(updated_b['balance_amount'], 5292.0)
        self.assertEqual(updated_b['total_amount'], 7042.0)
        self.assertEqual(updated_b['coupon_discount_applied'], 2100.0)
        self.assertEqual(updated_b['coupon_code'], 'RAFT-TEST')
        self.assertEqual(updated_b['booking_details']['advance_amount'], 1750.0)
        self.assertEqual(updated_b['booking_details']['balance_amount'], 5292.0)
        self.assertEqual(updated_b['booking_details']['total_amount'], 7042.0)
        self.assertEqual(updated_b['booking_details']['coupon_discount_applied'], 2100.0)
        self.assertEqual(updated_b['booking_details']['coupon_code'], 'RAFT-TEST')

if __name__ == '__main__':
    unittest.main()

