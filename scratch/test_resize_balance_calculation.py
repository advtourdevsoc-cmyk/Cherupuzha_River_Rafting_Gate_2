import sys
import os
from datetime import datetime
from bson.objectid import ObjectId

# Add parent dir to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

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

class SimpleMockDB:
    def __init__(self):
        self.settings = SimpleMockCollection()
        self.rafts = SimpleMockCollection()
        self.bookings = SimpleMockCollection()
        self.coupons = SimpleMockCollection()

def run_tests():
    db = SimpleMockDB()

    # Setup settings
    db.settings.insert_one({
        '_id': 'system_settings',
        'capacity': 6,
        'rafts_per_slot': 5,
        'time_slots': ['7:00am', '10:00am'],
        'weekday_amount': 1200,
        'saturday_amount': 1200,
        'weekday_advance_percent': 35,
        'saturday_advance_percent': 35,
        'platform_fee': 2.4
    })

    from utils.booking_ops import reduce_booking_group_size
    from utils.allocation_logic import allocate_raft

    date = '2026-10-10'
    slot = '7:00am'

    print("==================================================")
    print("TEST 1: NORMAL BOOKING (6 people @ 1200 -> reduce by 4 to 2 people)")
    print("==================================================")

    alloc1 = allocate_raft(db, None, date, slot, 6)
    b1_id = db.bookings.insert_one({
        'status': 'Confirmed',
        'payment_status': 'Paid',
        'date': date,
        'slot': slot,
        'group_size': 6,
        'amount_per_person': 1200,
        'price_amount': 7200.0,
        'advance_amount': 2520.0,
        'platform_fee': 60.48,
        'total_amount': 7260.48,
        'amount': 7260.48,
        'balance_amount': 4740.48,
        'raft_allocations': alloc1['rafts'],
        'raft_allocation_details': alloc1['raft_details'],
        'booking_details': {
            'date': date,
            'slot': slot,
            'group_size': 6,
            'amount_per_person': 1200,
            'price_amount': 7200.0,
            'advance_amount': 2520.0,
            'platform_fee': 60.48,
            'total_amount': 7260.48,
            'amount': 7260.48,
            'balance_amount': 4740.48
        }
    }).inserted_id

    # Reduce by 4 (new_size = 2)
    res1 = reduce_booking_group_size(db, b1_id, 2)
    print("Resize result:", res1)
    b1_after = db.bookings.find_one({'_id': b1_id})

    print(f"Group Size: {b1_after['group_size']}")
    print(f"Advance Amount (Fixed): {b1_after['advance_amount']}")
    print(f"Price Amount: {b1_after['price_amount']}")
    print(f"Balance Amount: {b1_after['balance_amount']}")
    print(f"Total Amount: {b1_after['total_amount']}")

    # Assertions for 6 to 2:
    # 4 * 1200 = 4800 reduced.
    # Old balance 4740.48 - 4800 = -59.52 (negative balance allowed)
    assert b1_after['group_size'] == 2
    assert b1_after['advance_amount'] == 2520.0, f"Expected 2520.0, got {b1_after['advance_amount']}"
    assert b1_after['balance_amount'] == -59.52, f"Expected -59.52, got {b1_after['balance_amount']}"
    assert b1_after['total_amount'] == 2460.48, f"Expected 2460.48, got {b1_after['total_amount']}"

    print("\n==================================================")
    print("TEST 2: NORMAL BOOKING (6 people -> reduce by 1 to 5 people)")
    print("==================================================")

    b2_id = db.bookings.insert_one({
        'status': 'Confirmed',
        'payment_status': 'Paid',
        'date': date,
        'slot': slot,
        'group_size': 6,
        'amount_per_person': 1200,
        'price_amount': 7200.0,
        'advance_amount': 2520.0,
        'platform_fee': 60.48,
        'total_amount': 7260.48,
        'amount': 7260.48,
        'balance_amount': 4740.48,
        'raft_allocations': alloc1['rafts'],
        'raft_allocation_details': alloc1['raft_details'],
        'booking_details': {
            'date': date,
            'slot': slot,
            'group_size': 6,
            'amount_per_person': 1200,
            'price_amount': 7200.0,
            'advance_amount': 2520.0,
            'platform_fee': 60.48,
            'total_amount': 7260.48,
            'amount': 7260.48,
            'balance_amount': 4740.48
        }
    }).inserted_id

    res2 = reduce_booking_group_size(db, b2_id, 5)
    print("Resize result:", res2)
    b2_after = db.bookings.find_one({'_id': b2_id})

    print(f"Group Size: {b2_after['group_size']}")
    print(f"Advance Amount (Fixed): {b2_after['advance_amount']}")
    print(f"Price Amount: {b2_after['price_amount']}")
    print(f"Balance Amount: {b2_after['balance_amount']}")
    print(f"Total Amount: {b2_after['total_amount']}")

    # Assertions for 6 to 5:
    # 1 * 1200 = 1200 reduced.
    # New balance = 4740.48 - 1200 = 3540.48
    # Total = 2520.0 + 3540.48 = 6060.48
    assert b2_after['group_size'] == 5
    assert b2_after['advance_amount'] == 2520.0
    assert b2_after['balance_amount'] == 3540.48, f"Expected 3540.48, got {b2_after['balance_amount']}"
    assert b2_after['total_amount'] == 6060.48, f"Expected 6060.48, got {b2_after['total_amount']}"

    print("\n==================================================")
    print("TEST 3: COUPON BOOKING (6 people -> reduce by 1 to 5 people with per_head coupon)")
    print("==================================================")

    db.coupons.insert_one({
        'code': 'RAFT-TEST100',
        'discount_type': 'per_head',
        'discount_value': 100
    })

    b3_id = db.bookings.insert_one({
        'status': 'Confirmed',
        'payment_status': 'Paid',
        'date': date,
        'slot': slot,
        'group_size': 6,
        'amount_per_person': 1200,
        'coupon_code': 'RAFT-TEST100',
        'coupon_discount_applied': 600.0,
        'price_amount': 6600.0,
        'advance_amount': 2310.0,
        'platform_fee': 55.44,
        'total_amount': 6655.44,
        'amount': 6655.44,
        'balance_amount': 4345.44,
        'raft_allocations': alloc1['rafts'],
        'raft_allocation_details': alloc1['raft_details'],
        'booking_details': {
            'date': date,
            'slot': slot,
            'group_size': 6,
            'amount_per_person': 1200,
            'coupon_code': 'RAFT-TEST100',
            'coupon_discount_applied': 600.0,
            'price_amount': 6600.0,
            'advance_amount': 2310.0,
            'platform_fee': 55.44,
            'total_amount': 6655.44,
            'amount': 6655.44,
            'balance_amount': 4345.44
        }
    }).inserted_id

    res3 = reduce_booking_group_size(db, b3_id, 5)
    print("Resize result:", res3)
    b3_after = db.bookings.find_one({'_id': b3_id})

    print(f"Group Size: {b3_after['group_size']}")
    print(f"Advance Amount (Fixed): {b3_after['advance_amount']}")
    print(f"Coupon Discount Applied: {b3_after['coupon_discount_applied']}")
    print(f"Price Amount: {b3_after['price_amount']}")
    print(f"Balance Amount: {b3_after['balance_amount']}")
    print(f"Total Amount: {b3_after['total_amount']}")

    assert b3_after['group_size'] == 5
    assert b3_after['advance_amount'] == 2310.0
    assert b3_after['coupon_discount_applied'] == 500.0
    assert b3_after['price_amount'] == 5500.0
    assert b3_after['balance_amount'] == 3245.44, f"Expected 3245.44, got {b3_after['balance_amount']}"
    assert b3_after['total_amount'] == 5555.44, f"Expected 5555.44, got {b3_after['total_amount']}"

    print("\nALL TESTS PASSED SUCCESSFULLY!")

if __name__ == '__main__':
    run_tests()
