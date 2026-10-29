# Script to fix booking status values in MongoDB
# Run this with your Flask app context or as a standalone script

from pymongo import MongoClient
from bson.objectid import ObjectId

# Update this URI if needed
MONGO_URI = "mongodb://127.0.0.1:27017/raft_booking"
client = MongoClient(MONGO_URI)
db = client.get_default_database() or client['raft_booking']

# List all unique status values
print("Current booking status values:")
for status in db.bookings.distinct('status'):
    print("-", status)

# Fix status values: change 'Paid', 'paid', 'confirmed', etc. to 'Confirmed' or 'paid'
fix_map = {
    'Paid': 'paid',
    'paid': 'paid',
    'confirmed': 'Confirmed',
    'CONFIRMED': 'Confirmed',
    'pending': 'Pending',
    'PENDING': 'Pending',
}

for old_status, new_status in fix_map.items():
    result = db.bookings.update_many({'status': old_status}, {'$set': {'status': new_status}})
    print(f"Updated {result.modified_count} bookings from '{old_status}' to '{new_status}'")

print("Status fix complete.")
