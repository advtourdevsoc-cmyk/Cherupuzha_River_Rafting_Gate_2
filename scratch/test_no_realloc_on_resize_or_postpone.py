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
from utils.booking_ops import reduce_booking_group_size, postpone_booking

print("==================================================")
print("TEST 1: RESIZING (REDUCING GROUP SIZE) NO REALLOCATION")
print("==================================================")

date_a = "2026-10-10"
slot_a = "7:00am"

# Create 5 bookings of size 6 on Rafts 1, 2, 3, 4, 5
b_ids = []
for i in range(1, 6):
    alloc = allocate_raft(db, None, date_a, slot_a, 6)
    bid = db.bookings.insert_one({
        'status': 'Confirmed',
        'date': date_a,
        'slot': slot_a,
        'group_size': 6,
        'created_at': datetime(2026, 10, 1, 10, i, 0),
        'raft_allocations': alloc['rafts'],
        'raft_allocation_details': alloc['raft_details'],
        'amount_per_person': 1000,
        'price_amount': 6000,
        'advance_amount': 1500,
        'total_amount': 6100,
        'balance_amount': 4600
    }).inserted_id
    b_ids.append(bid)

print("Initial Allocations:")
for idx, bid in enumerate(b_ids, 1):
    b = db.bookings.find_one({'_id': bid})
    print(f"Booking {idx}: size {b['group_size']}, rafts: {b['raft_allocations']}")

# Now resize Booking 1 from 6 to 3
print("\nResizing Booking 1 from 6 to 3...")
reduce_booking_group_size(db, b_ids[0], 3)

print("\nAllocations after resizing Booking 1:")
for idx, bid in enumerate(b_ids, 1):
    b = db.bookings.find_one({'_id': bid})
    print(f"Booking {idx}: size {b['group_size']}, rafts: {b['raft_allocations']}")

# Assertions for Resize
assert db.bookings.find_one({'_id': b_ids[0]})['raft_allocations'] == [1]
assert db.bookings.find_one({'_id': b_ids[1]})['raft_allocations'] == [2]
assert db.bookings.find_one({'_id': b_ids[2]})['raft_allocations'] == [3]
assert db.bookings.find_one({'_id': b_ids[3]})['raft_allocations'] == [4]
assert db.bookings.find_one({'_id': b_ids[4]})['raft_allocations'] == [5]
print("--> TEST 1 PASSED: Resizing affected ONLY Booking 1. Bookings 2-5 remained untouched!")


print("\n==================================================")
print("TEST 2: POSTPONING NO REALLOCATION ON OLD OR NEW SLOT")
print("==================================================")

date_b = "2026-10-15"
slot_b = "7:00am"

# Slot B has 2 existing bookings
alloc_x = allocate_raft(db, None, date_b, slot_b, 6)
bx_id = db.bookings.insert_one({
    'status': 'Confirmed',
    'date': date_b,
    'slot': slot_b,
    'group_size': 6,
    'created_at': datetime(2026, 10, 1, 12, 0, 0),
    'raft_allocations': alloc_x['rafts'],
    'raft_allocation_details': alloc_x['raft_details']
}).inserted_id

alloc_y = allocate_raft(db, None, date_b, slot_b, 5)
by_id = db.bookings.insert_one({
    'status': 'Confirmed',
    'date': date_b,
    'slot': slot_b,
    'group_size': 5,
    'created_at': datetime(2026, 10, 1, 12, 1, 0),
    'raft_allocations': alloc_y['rafts'],
    'raft_allocation_details': alloc_y['raft_details']
}).inserted_id

print(f"Initial Slot B ({date_b}):")
print(f"Booking X rafts: {db.bookings.find_one({'_id': bx_id})['raft_allocations']}")
print(f"Booking Y rafts: {db.bookings.find_one({'_id': by_id})['raft_allocations']}")

# Postpone Booking 2 (from Slot A) to Slot B
print(f"\nPostponing Booking 2 from {date_a} to {date_b}...")
res_postpone = postpone_booking(db, b_ids[1], date_b, slot_b)
print("Postpone result:", res_postpone['message'])

print(f"\nSlot A ({date_a}) after postpone:")
for idx in [0, 2, 3, 4]:
    b = db.bookings.find_one({'_id': b_ids[idx]})
    print(f"Booking {idx+1}: rafts: {b['raft_allocations']}")

print(f"\nSlot B ({date_b}) after postpone:")
print(f"Booking X rafts: {db.bookings.find_one({'_id': bx_id})['raft_allocations']}")
print(f"Booking Y rafts: {db.bookings.find_one({'_id': by_id})['raft_allocations']}")
print(f"Postponed Booking 2 rafts: {db.bookings.find_one({'_id': b_ids[1]})['raft_allocations']}")

# Assertions for Postpone
# Slot A: Booking 1 on [1], Booking 3 on [3], Booking 4 on [4], Booking 5 on [5]
assert db.bookings.find_one({'_id': b_ids[0]})['raft_allocations'] == [1]
assert db.bookings.find_one({'_id': b_ids[2]})['raft_allocations'] == [3]
assert db.bookings.find_one({'_id': b_ids[3]})['raft_allocations'] == [4]
assert db.bookings.find_one({'_id': b_ids[4]})['raft_allocations'] == [5]

# Slot B: Booking X on [1], Booking Y on [2], Postponed Booking 2 placed on [3]
assert db.bookings.find_one({'_id': bx_id})['raft_allocations'] == [1]
assert db.bookings.find_one({'_id': by_id})['raft_allocations'] == [2]
assert db.bookings.find_one({'_id': b_ids[1]})['raft_allocations'] == [3]

print("--> TEST 2 PASSED: Postponing did NOT reshuffle existing bookings on old OR new slot!")
print("\nALL NO-REALLOCATION TESTS PASSED SUCCESSFULLY!")
