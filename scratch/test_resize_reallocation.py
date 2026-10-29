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

date = "2026-10-02"
slot = "7:00am"

# Create booking 1: size 7
alloc1 = allocate_raft(db, None, date, slot, 7)
b1_id = db.bookings.insert_one({
    'status': 'Confirmed',
    'date': date,
    'slot': slot,
    'group_size': 7,
    'created_at': datetime(2026, 10, 1, 19, 36, 0),
    'raft_allocations': alloc1['rafts'],
    'raft_allocation_details': alloc1['raft_details'],
    'amount_per_person': 1000,
    'price_amount': 7000,
    'advance_amount': 1750,
    'total_amount': 7100,
    'balance_amount': 5350
}).inserted_id

# Create booking 2: size 5
alloc2 = allocate_raft(db, None, date, slot, 5)
b2_id = db.bookings.insert_one({
    'status': 'Confirmed',
    'date': date,
    'slot': slot,
    'group_size': 5,
    'created_at': datetime(2026, 10, 1, 19, 37, 0),
    'raft_allocations': alloc2['rafts'],
    'raft_allocation_details': alloc2['raft_details'],
    'amount_per_person': 1000,
    'price_amount': 5000,
    'advance_amount': 1250,
    'total_amount': 5100,
    'balance_amount': 3850
}).inserted_id

# Create booking 3: size 7
alloc3 = allocate_raft(db, None, date, slot, 7)
b3_id = db.bookings.insert_one({
    'status': 'Confirmed',
    'date': date,
    'slot': slot,
    'group_size': 7,
    'created_at': datetime(2026, 10, 1, 19, 38, 0),
    'raft_allocations': alloc3['rafts'],
    'raft_allocation_details': alloc3['raft_details'],
    'amount_per_person': 1000,
    'price_amount': 7000,
    'advance_amount': 1750,
    'total_amount': 7100,
    'balance_amount': 5350
}).inserted_id

print("=== BEFORE RESIZE ===")
print("Booking 1 rafts:", db.bookings.find_one({'_id': b1_id})['raft_allocations'])
print("Booking 2 rafts:", db.bookings.find_one({'_id': b2_id})['raft_allocations'])
print("Booking 3 rafts:", db.bookings.find_one({'_id': b3_id})['raft_allocations'])
for r in db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1):
    print(f"Raft {r['raft_id']}: {r['occupancy']}/{r.get('capacity', 6)} (is_special: {r.get('is_special', False)})")

# RESIZE booking 3 from 7 to 3
res = reduce_booking_group_size(db, b3_id, 3)
print("\n=== RESIZE RESULT ===", res)

print("\n=== AFTER RESIZE ===")
b1 = db.bookings.find_one({'_id': b1_id})
b2 = db.bookings.find_one({'_id': b2_id})
b3 = db.bookings.find_one({'_id': b3_id})
print("Booking 1 rafts:", b1['raft_allocations'], b1['raft_allocation_details'])
print("Booking 2 rafts:", b2['raft_allocations'], b2['raft_allocation_details'])
print("Booking 3 rafts:", b3['raft_allocations'], b3['raft_allocation_details'])
for r in db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1):
    print(f"Raft {r['raft_id']}: {r['occupancy']}/{r.get('capacity', 6)} (is_special: {r.get('is_special', False)})")

assert b1['raft_allocations'] == [1], f"Expected b1 on Raft 1, got {b1['raft_allocations']}"
assert b2['raft_allocations'] == [2], f"Expected b2 on Raft 2, got {b2['raft_allocations']}"
assert b3['raft_allocations'] == [3], f"Expected b3 on Raft 3 alone, got {b3['raft_allocations']}"

rafts_map = {r['raft_id']: r['occupancy'] for r in db.rafts.find({'day': date, 'slot': slot})}
assert rafts_map[1] == 7
assert rafts_map[2] == 5
assert rafts_map[3] == 3
assert rafts_map[4] == 0
assert rafts_map[5] == 0

print("\nALL REALLOCATION ASSERTIONS PASSED SUCCESSFULLY!")
