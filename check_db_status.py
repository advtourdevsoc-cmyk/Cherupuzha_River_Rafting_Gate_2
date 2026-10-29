from pymongo import MongoClient
from config import MONGO_URI

client = MongoClient(MONGO_URI)
db = client.get_database('raft_booking')

print('=== Database Status ===')
print(f'Bookings count: {db.bookings.count_documents({})}')
print(f'Payments count: {db.payments.count_documents({})}')
print(f'Rafts count: {db.rafts.count_documents({})}')
print(f'Users count: {db.users.count_documents({})}')

# Check all bookings
bookings = list(db.bookings.find({}).limit(5))
if bookings:
    print(f'\nSample bookings:')
    for b in bookings:
        print(f'  - {b.get("date")} {b.get("slot")}: {b.get("group_size")} people')
else:
    print('\n✅ No bookings in database (clean)')

# Check occupancy for April 19
print('\n=== April 19 Occupancy ===')
for slot in ['7:00am', '10:00am', '1:00pm', '3:30pm']:
    rafts = list(db.rafts.find({'day': '2026-04-19', 'slot': slot}))
    total_occ = sum(r.get('occupancy', 0) for r in rafts[:5])  # Only first 5
    print(f'{slot}: {total_occ} occupied in 5 rafts')
