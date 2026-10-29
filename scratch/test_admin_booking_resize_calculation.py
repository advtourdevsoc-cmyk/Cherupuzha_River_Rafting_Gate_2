import sys
import os
import unittest
from bson.objectid import ObjectId
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.amount_calculator import recalculate_booking_resize_amounts, calculate_total_amount
from utils.booking_ops import reduce_booking_group_size
from utils.allocation_logic import allocate_raft


class SimpleMockCollection:
    def __init__(self):
        self.docs = []

    def insert_many(self, docs):
        for doc in docs:
            self.insert_one(doc)

    def insert_one(self, doc):
        if '_id' not in doc:
            doc['_id'] = ObjectId()
        self.docs.append(doc)
        class Res:
            pass
        r = Res()
        r.inserted_id = doc['_id']
        return r

    def find_one(self, query):
        for d in self.docs:
            match = True
            for k, v in query.items():
                if k.startswith('$'):
                    continue
                if d.get(k) != v:
                    match = False
                    break
            if match:
                return d
        return None

    def find(self, query=None):
        if query is None:
            query = {}
        matched = []
        for d in self.docs:
            match = True
            for k, v in query.items():
                if k.startswith('$'):
                    continue
                if d.get(k) != v:
                    match = False
                    break
            if match:
                matched.append(d)

        class Cursor:
            def __init__(self, data):
                self.data = data
            def sort(self, key, direction=1):
                self.data.sort(key=lambda x: x.get(key, 0), reverse=(direction == -1))
                return self
            def limit(self, n):
                self.data = self.data[:n]
                return self
            def __iter__(self):
                return iter(self.data)
            def __list__(self):
                return self.data

        return Cursor(matched)

    def update_one(self, filter_query, update_query):
        doc = self.find_one(filter_query)
        if not doc:
            return
        if '$set' in update_query:
            for k, v in update_query['$set'].items():
                if '.' in k:
                    parts = k.split('.')
                    target = doc
                    for p in parts[:-1]:
                        if p not in target or not isinstance(target[p], dict):
                            target[p] = {}
                        target = target[p]
                    target[parts[-1]] = v
                else:
                    doc[k] = v
        if '$inc' in update_query:
            for k, v in update_query['$inc'].items():
                doc[k] = doc.get(k, 0) + v

    def update_many(self, filter_query, update_query):
        for doc in self.docs:
            match = True
            for k, v in filter_query.items():
                if doc.get(k) != v:
                    match = False
                    break
            if match:
                if '$set' in update_query:
                    for k, v in update_query['$set'].items():
                        if '.' in k:
                            parts = k.split('.')
                            target = doc
                            for p in parts[:-1]:
                                if p not in target or not isinstance(target[p], dict):
                                    target[p] = {}
                                target = target[p]
                            target[parts[-1]] = v
                        else:
                            doc[k] = v
                if '$inc' in update_query:
                    for k, v in update_query['$inc'].items():
                        doc[k] = doc.get(k, 0) + v


class MockDB:
    def __init__(self):
        self.settings = SimpleMockCollection()
        self.rafts = SimpleMockCollection()
        self.bookings = SimpleMockCollection()
        self.coupons = SimpleMockCollection()


class TestAdminBookingResizeAmountCalculation(unittest.TestCase):
    def setUp(self):
        self.db = MockDB()
        self.date = '2026-10-20'  # Tuesday (weekday)
        self.slot = '7:00am'
        self.settings = {
            'weekday_amount': 1000,
            'saturday_amount': 1200,
            'weekday_advance_percent': 25,
            'saturday_advance_percent': 35,
            'platform_fee': 2.4,
            'capacity': 6,
            'rafts_per_slot': 5,
            'time_slots': ['7:00am', '10:00am']
        }
        self.db.settings.insert_one(self.settings)

        # Initialize rafts for date/slot
        for i in range(1, 6):
            self.db.rafts.insert_one({
                'day': self.date,
                'slot': self.slot,
                'raft_id': i,
                'occupancy': 0,
                'is_special': False
            })

    def _create_admin_booking(self, standard_price=1000, per_person_discount=100,
                              group_size=10, advance_amount=2000):
        effective_per_person = max(0.0, standard_price - per_person_discount)
        price_amount = round(effective_per_person * group_size, 2)
        total_amount = price_amount
        balance_amount = round(total_amount - advance_amount, 2)

        alloc = allocate_raft(self.db, None, self.date, self.slot, group_size)

        booking_doc = {
            'status': 'Confirmed',
            'payment_status': 'Paid',
            'date': self.date,
            'slot': self.slot,
            'group_size': group_size,
            'is_admin_booking': True,
            'standard_amount_per_person': standard_price,
            'per_person_discount': per_person_discount,
            'amount_per_person': effective_per_person,
            'price_amount': price_amount,
            'platform_fee': 0.0,
            'total_amount': total_amount,
            'amount': total_amount,
            'advance_amount': advance_amount,
            'balance_amount': balance_amount,
            'raft_allocations': alloc.get('rafts', []),
            'raft_allocation_details': alloc.get('raft_details', []),
            'booking_details': {
                'date': self.date,
                'slot': self.slot,
                'group_size': group_size,
                'is_admin_booking': True,
                'standard_amount_per_person': standard_price,
                'per_person_discount': per_person_discount,
                'amount_per_person': effective_per_person,
                'price_amount': price_amount,
                'platform_fee': 0.0,
                'total_amount': total_amount,
                'amount': total_amount,
                'advance_amount': advance_amount,
                'balance_amount': balance_amount,
                'raft_allocations': alloc.get('rafts', []),
                'raft_allocation_details': alloc.get('raft_details', [])
            }
        }
        res = self.db.bookings.insert_one(booking_doc)
        return res.inserted_id

    def test_1_resize_admin_booking_with_discount_and_advance(self):
        """Case 1: Standard example from user prompt:
        Standard: ₹1,000, Discount: ₹100, Original: 10, Updated: 6, Advance: ₹2,000
        Expected: Discounted: ₹900, New total: ₹5,400, Advance: ₹2,000, Balance: ₹3,400
        """
        b_id = self._create_admin_booking(standard_price=1000, per_person_discount=100,
                                          group_size=10, advance_amount=2000)
        res = reduce_booking_group_size(self.db, b_id, 6)
        self.assertNotIn('error', res)

        b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b['group_size'], 6)
        self.assertEqual(b['standard_amount_per_person'], 1000.0)
        self.assertEqual(b['per_person_discount'], 100.0)
        self.assertEqual(b['amount_per_person'], 900.0)
        self.assertEqual(b['total_amount'], 5400.0)
        self.assertEqual(b['price_amount'], 5400.0)
        self.assertEqual(b['amount'], 5400.0)
        self.assertEqual(b['advance_amount'], 2000.0)
        self.assertEqual(b['balance_amount'], 3400.0)
        self.assertEqual(b['platform_fee'], 0.0)
        self.assertEqual(b['coupon_discount_applied'], 0.0)

        # Check booking_details consistency
        bd = b['booking_details']
        self.assertEqual(bd['group_size'], 6)
        self.assertEqual(bd['standard_amount_per_person'], 1000.0)
        self.assertEqual(bd['per_person_discount'], 100.0)
        self.assertEqual(bd['amount_per_person'], 900.0)
        self.assertEqual(bd['total_amount'], 5400.0)
        self.assertEqual(bd['advance_amount'], 2000.0)
        self.assertEqual(bd['balance_amount'], 3400.0)

    def test_2_resize_admin_booking_with_discount_no_advance(self):
        """Case 2: Discount but no advance.
        Standard: ₹1,000, Discount: ₹150, Original: 8, Updated: 4, Advance: 0
        Expected: Discounted: ₹850, New total: ₹3,400, Advance: 0, Balance: ₹3,400
        """
        b_id = self._create_admin_booking(standard_price=1000, per_person_discount=150,
                                          group_size=8, advance_amount=0)
        res = reduce_booking_group_size(self.db, b_id, 4)
        self.assertNotIn('error', res)

        b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b['group_size'], 4)
        self.assertEqual(b['amount_per_person'], 850.0)
        self.assertEqual(b['total_amount'], 3400.0)
        self.assertEqual(b['advance_amount'], 0.0)
        self.assertEqual(b['balance_amount'], 3400.0)

    def test_3_resize_admin_booking_with_advance_no_discount(self):
        """Case 3: Advance but no discount.
        Standard: ₹1,200, Discount: 0, Original: 8, Updated: 5, Advance: ₹3,000
        Expected: Discounted: ₹1,200, New total: ₹6,000, Advance: ₹3,000, Balance: ₹3,000
        """
        b_id = self._create_admin_booking(standard_price=1200, per_person_discount=0,
                                          group_size=8, advance_amount=3000)
        res = reduce_booking_group_size(self.db, b_id, 5)
        self.assertNotIn('error', res)

        b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b['group_size'], 5)
        self.assertEqual(b['amount_per_person'], 1200.0)
        self.assertEqual(b['total_amount'], 6000.0)
        self.assertEqual(b['advance_amount'], 3000.0)
        self.assertEqual(b['balance_amount'], 3000.0)

    def test_4_resize_admin_booking_neither_discount_nor_advance(self):
        """Case 4: Neither discount nor advance.
        Standard: ₹1,000, Discount: 0, Original: 6, Updated: 3, Advance: 0
        Expected: Discounted: ₹1,000, New total: ₹3,000, Advance: 0, Balance: ₹3,000
        """
        b_id = self._create_admin_booking(standard_price=1000, per_person_discount=0,
                                          group_size=6, advance_amount=0)
        res = reduce_booking_group_size(self.db, b_id, 3)
        self.assertNotIn('error', res)

        b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b['group_size'], 3)
        self.assertEqual(b['amount_per_person'], 1000.0)
        self.assertEqual(b['total_amount'], 3000.0)
        self.assertEqual(b['advance_amount'], 0.0)
        self.assertEqual(b['balance_amount'], 3000.0)

    def test_5_multiple_consecutive_group_size_reductions(self):
        """Case 5: Reduce group size multiple times and verify discount is not applied repeatedly.
        Start: 10 people @ 1000 with 100 discount, advance 2000. Total 9000, Balance 7000.
        Reduce 1: 10 -> 8: Total 7200, Balance 5200.
        Reduce 2: 8 -> 6: Total 5400, Balance 3400.
        Reduce 3: 6 -> 4: Total 3600, Balance 1600.
        """
        b_id = self._create_admin_booking(standard_price=1000, per_person_discount=100,
                                          group_size=10, advance_amount=2000)

        # Step 1: 10 -> 8
        res1 = reduce_booking_group_size(self.db, b_id, 8)
        self.assertNotIn('error', res1)
        b1 = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b1['group_size'], 8)
        self.assertEqual(b1['amount_per_person'], 900.0)
        self.assertEqual(b1['total_amount'], 7200.0)
        self.assertEqual(b1['advance_amount'], 2000.0)
        self.assertEqual(b1['balance_amount'], 5200.0)

        # Step 2: 8 -> 6
        res2 = reduce_booking_group_size(self.db, b_id, 6)
        self.assertNotIn('error', res2)
        b2 = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b2['group_size'], 6)
        self.assertEqual(b2['amount_per_person'], 900.0)  # Must NOT be 800!
        self.assertEqual(b2['total_amount'], 5400.0)
        self.assertEqual(b2['advance_amount'], 2000.0)
        self.assertEqual(b2['balance_amount'], 3400.0)

        # Step 3: 6 -> 4
        res3 = reduce_booking_group_size(self.db, b_id, 4)
        self.assertNotIn('error', res3)
        b3 = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b3['group_size'], 4)
        self.assertEqual(b3['amount_per_person'], 900.0)
        self.assertEqual(b3['total_amount'], 3600.0)
        self.assertEqual(b3['advance_amount'], 2000.0)
        self.assertEqual(b3['balance_amount'], 1600.0)

    def test_6_increase_group_size(self):
        """Case 6: Increase group size and verify calculation remains correct.
        Start: 4 people @ 1000 with 100 discount, advance 1500. Total 3600, Balance 2100.
        Increase: 4 -> 6:
        Expected: Discounted: 900, New total: 900 * 6 = 5400, Advance: 1500, Balance: 3900.
        """
        b_id = self._create_admin_booking(standard_price=1000, per_person_discount=100,
                                          group_size=4, advance_amount=1500)
        res = reduce_booking_group_size(self.db, b_id, 6)
        self.assertNotIn('error', res)

        b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b['group_size'], 6)
        self.assertEqual(b['amount_per_person'], 900.0)
        self.assertEqual(b['total_amount'], 5400.0)
        self.assertEqual(b['advance_amount'], 1500.0)
        self.assertEqual(b['balance_amount'], 3900.0)

    def test_7_resized_total_equal_to_advance_paid(self):
        """Case 7: Resized total equal to advance already paid.
        Standard: 1000, Discount: 0, Advance: 2000.
        Original: 6 -> Total 6000, Balance 4000.
        Resize to 2:
        New total = 1000 * 2 = 2000. Advance = 2000.
        Remaining balance = 2000 - 2000 = 0.0.
        """
        b_id = self._create_admin_booking(standard_price=1000, per_person_discount=0,
                                          group_size=6, advance_amount=2000)
        res = reduce_booking_group_size(self.db, b_id, 2)
        self.assertNotIn('error', res)

        b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b['group_size'], 2)
        self.assertEqual(b['total_amount'], 2000.0)
        self.assertEqual(b['advance_amount'], 2000.0)
        self.assertEqual(b['balance_amount'], 0.0)

    def test_8_resized_total_lower_than_advance_paid_overpayment(self):
        """Case 8: Resized total lower than advance already paid (overpayment/refund).
        Standard: 1000, Discount: 100, Advance: 2000.
        Original: 6.
        Resize to 1:
        New total = 900 * 1 = 900.
        Advance = 2000.
        Remaining balance = 900 - 2000 = -1100.0.
        Advance must remain 2000.0, balance must reflect -1100.0 (overpayment/refund).
        """
        b_id = self._create_admin_booking(standard_price=1000, per_person_discount=100,
                                          group_size=6, advance_amount=2000)
        res = reduce_booking_group_size(self.db, b_id, 1)
        self.assertNotIn('error', res)

        b = self.db.bookings.find_one({'_id': b_id})
        self.assertEqual(b['group_size'], 1)
        self.assertEqual(b['total_amount'], 900.0)
        self.assertEqual(b['advance_amount'], 2000.0)
        self.assertEqual(b['balance_amount'], -1100.0)

    def test_9_customer_coupon_bookings_unaffected(self):
        """Case 9: Verify customer bookings using coupon discounts continue to calculate correctly."""
        # 1. Customer booking with per_head coupon
        self.db.coupons.insert_one({
            'code': 'RAFT-HEAD100',
            'discount_type': 'per_head',
            'discount_value': 100
        })

        alloc = allocate_raft(self.db, None, self.date, self.slot, 6)
        cust_b_id = self.db.bookings.insert_one({
            'status': 'Confirmed',
            'payment_status': 'Paid',
            'date': self.date,
            'slot': self.slot,
            'group_size': 6,
            'is_admin_booking': False,
            'amount_per_person': 1000.0,
            'coupon_code': 'RAFT-HEAD100',
            'coupon_discount_applied': 600.0,
            'price_amount': 5400.0,
            'advance_amount': 1350.0,
            'platform_fee': 32.40,
            'total_amount': 5432.40,
            'amount': 5432.40,
            'balance_amount': 4082.40,
            'raft_allocations': alloc['rafts'],
            'raft_allocation_details': alloc['raft_details'],
            'booking_details': {
                'date': self.date,
                'slot': self.slot,
                'group_size': 6,
                'amount_per_person': 1000.0,
                'coupon_code': 'RAFT-HEAD100',
                'coupon_discount_applied': 600.0,
                'price_amount': 5400.0,
                'advance_amount': 1350.0,
                'platform_fee': 32.40,
                'total_amount': 5432.40,
                'amount': 5432.40,
                'balance_amount': 4082.40
            }
        }).inserted_id

        # Reduce customer booking from 6 to 5
        res = reduce_booking_group_size(self.db, cust_b_id, 5)
        self.assertNotIn('error', res)

        cust_b = self.db.bookings.find_one({'_id': cust_b_id})
        self.assertEqual(cust_b['group_size'], 5)
        self.assertEqual(cust_b['coupon_discount_applied'], 500.0)
        self.assertEqual(cust_b['price_amount'], 4500.0)
        self.assertEqual(cust_b['advance_amount'], 1350.0)
        self.assertEqual(cust_b['platform_fee'], 32.40)
        self.assertEqual(cust_b['total_amount'], 4532.40)
        self.assertEqual(cust_b['balance_amount'], 3182.40)

    def test_10_data_consistency_across_all_fields(self):
        """Case 10: Verify data consistency across root fields and booking_details."""
        b_id = self._create_admin_booking(standard_price=1000, per_person_discount=100,
                                          group_size=10, advance_amount=2000)
        reduce_booking_group_size(self.db, b_id, 6)
        b = self.db.bookings.find_one({'_id': b_id})
        bd = b['booking_details']

        for key in ['group_size', 'standard_amount_per_person', 'per_person_discount',
                    'amount_per_person', 'price_amount', 'total_amount', 'amount',
                    'advance_amount', 'balance_amount']:
            self.assertEqual(b[key], bd[key], f"Mismatch in field {key}: root={b[key]}, details={bd[key]}")


if __name__ == '__main__':
    unittest.main()
