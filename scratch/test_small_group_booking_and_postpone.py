import sys
import os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.effective_availability import is_group_size_allocatable, calculate_effective_available_capacity_from_rafts
from utils.allocation_logic import allocate_raft
from utils.booking_ops import check_capacity_available, postpone_booking, reduce_booking_group_size

class SimpleMockCollection:
    def __init__(self):
        self.docs = []

    def count_documents(self, query):
        count = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                count += 1
        return count

    def find_one(self, query):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None

    def find(self, query):
        res = []
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                res.append(d)
        return MockCursor(res)

    def update_one(self, filter_q, update_q):
        doc = self.find_one(filter_q)
        if doc:
            if '$set' in update_q:
                for k, v in update_q['$set'].items():
                    doc[k] = v
            if '$inc' in update_q:
                for k, v in update_q['$inc'].items():
                    doc[k] = doc.get(k, 0)
            return True
        return False

    def update_many(self, filter_q, update_q):
        count = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in filter_q.items()):
                if '$set' in update_q:
                    for k, v in update_q['$set'].items():
                        d[k] = v
                count += 1
        return count

    def insert_many(self, docs):
        for idx, d in enumerate(docs):
            if '_id' not in d:
                d['_id'] = f"mock_id_{len(self.docs) + idx}"
        self.docs.extend(docs)
        return True

class MockCursor:
    def __init__(self, items):
        self.items = items

    def sort(self, key, direction=1):
        self.items.sort(key=lambda x: x.get(key, 0), reverse=(direction == -1))
        return self

    def limit(self, n):
        self.items = self.items[:n]
        return self

    def __iter__(self):
        return iter(self.items)

    def __list__(self):
        return self.items

class MockDB:
    def __init__(self):
        self.rafts = SimpleMockCollection()
        self.bookings = SimpleMockCollection()
        self.settings = SimpleMockCollection()
        self.settings.docs.append({
            '_id': 'system_settings',
            'rafts_per_slot': 5,
            'capacity': 6,
            'time_slots': ['7:00am', '10:00am', '1:00pm', '3:30pm'],
            'start_date': date.today().isoformat(),
            'days': 30
        })

def test_new_booking_vs_admin_resizing_and_postpone():
    print("--- TEST: New Customer Booking vs Admin Resizing & Postpone ---")
    db = MockDB()
    date_a = '2026-10-10'
    date_b = '2026-10-15'
    slot = '10:00am'

    # 1. New Customer Booking (< 4) on Empty Slot: MUST BE BLOCKED (allow_empty_for_small_group=False)
    has_cap_new = check_capacity_available(db, date_a, slot, 3, allow_empty_for_small_group=False)
    print("New customer booking check_capacity for group size 3 on empty slot:", has_cap_new)
    assert has_cap_new == False, "New customer booking < 4 should NOT be allowed on empty slot"

    res_new_alloc = allocate_raft(db, 'cust1@example.com', date_a, slot, 3, allow_empty_for_small_group=False)
    print("New customer allocate_raft for group size 3 on empty slot:", res_new_alloc['status'])
    assert res_new_alloc['status'] == 'Pending'

    # 2. Resizing a booking of 8 to 3 in Admin Panel: MUST SUCCEED (allow_empty_for_small_group=True)
    res_8 = allocate_raft(db, 'user8@example.com', date_a, slot, 8)
    assert res_8['status'] == 'Confirmed'

    b_8 = {
        '_id': 'booking_8',
        'email': 'user8@example.com',
        'date': date_a,
        'slot': slot,
        'group_size': 8,
        'status': 'Confirmed',
        'raft_allocations': res_8['rafts'],
        'raft_allocation_details': res_8['raft_details']
    }
    db.bookings.docs.append(b_8)

    # Admin resizes booking from 8 to 3
    resize_res = reduce_booking_group_size(db, 'booking_8', 3)
    print("Admin resizing booking 8 to 3 result:", resize_res)
    assert 'error' not in resize_res, "Resizing booking from 8 to 3 in admin panel MUST succeed!"

    # Verify booking 8 updated to 3
    b_8_after = db.bookings.find_one({'_id': 'booking_8'})
    assert b_8_after['group_size'] == 3
    assert b_8_after['raft_allocations'] == [1]

    # 3. Postponing small booking of 3 to date_b (empty slot): MUST SUCCEED (allow_empty_for_small_group=True)
    post_res = postpone_booking(db, 'booking_8', date_b, slot)
    print("Postponing resized booking 3 to empty slot date_b:", post_res['message'])
    assert 'error' not in post_res

    print("ALL ADMIN RESIZING & POSTPONE TESTS PASSED SUCCESSFULLY!")

if __name__ == '__main__':
    test_new_booking_vs_admin_resizing_and_postpone()
