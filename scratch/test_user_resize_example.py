from datetime import datetime
from bson.objectid import ObjectId

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
                        doc[k] = v

class SimpleMockDB:
    def __init__(self):
        self.settings = SimpleMockCollection()
        self.rafts = SimpleMockCollection()
        self.bookings = SimpleMockCollection()

db = SimpleMockDB()

db.settings.insert_one({
    '_id': 'system_settings',
    'rafts_per_slot': 5,
    'capacity': 6,
    'time_slots': ['7:00am', '10:00am', '1:00pm', '3:30pm']
})

from utils.allocation_logic import allocate_raft
from utils.booking_ops import reduce_booking_group_size

date = "2026-10-20"
slot = "7:00am"

print("==================================================")
print("TEST: USER EXAMPLE - RESIZING GROUP FROM 8 TO 7")
print("==================================================")

# 1. Create Booking 1 of size 8 (allocated as [6, 2])
alloc1 = allocate_raft(db, None, date, slot, 8)
b1_id = db.bookings.insert_one({
    'status': 'Confirmed',
    'date': date,
    'slot': slot,
    'group_size': 8,
    'created_at': datetime(2026, 10, 1, 10, 0, 0),
    'raft_allocations': alloc1['rafts'],
    'raft_allocation_details': alloc1['raft_details'],
    'amount_per_person': 1000,
    'price_amount': 8000,
    'advance_amount': 2000,
    'total_amount': 8100,
    'balance_amount': 6100
}).inserted_id

# 2. Create Booking 2 of size 5 on Raft 3
alloc2 = allocate_raft(db, None, date, slot, 5)
b2_id = db.bookings.insert_one({
    'status': 'Confirmed',
    'date': date,
    'slot': slot,
    'group_size': 5,
    'created_at': datetime(2026, 10, 1, 10, 1, 0),
    'raft_allocations': alloc2['rafts'],
    'raft_allocation_details': alloc2['raft_details'],
    'amount_per_person': 1000,
    'price_amount': 5000,
    'advance_amount': 1250,
    'total_amount': 5100,
    'balance_amount': 3850
}).inserted_id

print("\n--- INITIAL STATE ---")
b1 = db.bookings.find_one({'_id': b1_id})
b2 = db.bookings.find_one({'_id': b2_id})
print(f"Booking 1 (size {b1['group_size']}): rafts {b1['raft_allocations']}, details: {b1['raft_allocation_details']}")
print(f"Booking 2 (size {b2['group_size']}): rafts {b2['raft_allocations']}, details: {b2['raft_allocation_details']}")
for r in db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1):
    print(f"Raft {r['raft_id']}: {r['occupancy']}/6 (is_special: {r.get('is_special', False)})")

# Assert initial state
assert b1['raft_allocations'] == [1, 2]
assert b2['raft_allocations'] == [3]

# 3. Reduce Booking 1 group size from 8 to 7
print("\n--- REDUCING BOOKING 1 FROM 8 TO 7 ---")
res = reduce_booking_group_size(db, b1_id, 7)
print("Result:", res)

b1_after = db.bookings.find_one({'_id': b1_id})
b2_after = db.bookings.find_one({'_id': b2_id})

print("\n--- AFTER RESIZE ---")
print(f"Booking 1 (size {b1_after['group_size']}): rafts {b1_after['raft_allocations']}, details: {b1_after['raft_allocation_details']}")
print(f"Booking 2 (size {b2_after['group_size']}): rafts {b2_after['raft_allocations']}, details: {b2_after['raft_allocation_details']}")
for r in db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1):
    print(f"Raft {r['raft_id']}: {r['occupancy']}/6 (is_special: {r.get('is_special', False)})")

# Assertions:
# 1. Booking 1 is now size 7, allocated to Raft 1 alone ([1]), details count 7
assert b1_after['group_size'] == 7
assert b1_after['raft_allocations'] == [1]
assert b1_after['raft_allocation_details'] == [{'raft_id': 1, 'count': 7}]

# 2. Booking 2 is UNTOUCHED on Raft 3
assert b2_after['raft_allocations'] == [3]

# 3. Raft states: Raft 1 is 7 (special), Raft 2 is 0 (freed), Raft 3 is 5
rafts_map = {r['raft_id']: r['occupancy'] for r in db.rafts.find({'day': date, 'slot': slot})}
assert rafts_map[1] == 7
assert rafts_map[2] == 0
assert rafts_map[3] == 5

print("\nALL USER EXAMPLE ASSERTIONS PASSED PERFECTLY!")
