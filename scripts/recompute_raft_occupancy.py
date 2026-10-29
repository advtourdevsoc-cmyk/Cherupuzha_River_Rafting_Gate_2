from pymongo import MongoClient
import sys
import os

# Use project config to get the MongoDB URI (avoid falling back to localhost on servers)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import MONGO_URI
client = MongoClient(MONGO_URI)
db = client.get_database("raft_booking")

settings = db.settings.find_one({'_id':'system_settings'}) or {'rafts_per_slot':5,'capacity':6}
capacity = settings.get('capacity', 6)
rafts_per_slot = settings.get('rafts_per_slot', 5)

print("Resetting all raft occupancy to 0...")
db.rafts.update_many({}, {'$set': {'occupancy': 0, 'is_special': False}})

# Ensure rafts exist for all booking dates & slots
bookings = list(db.bookings.find({}))
dates_slots = set()
for b in bookings:
    dates_slots.add((b.get('date'), b.get('slot')))

def ensure_rafts_for_date_slot(db, date, slot, rafts_per_slot, capacity):
    existing = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1))
    existing_ids = {r['raft_id'] for r in existing}
    to_create = []
    for rid in range(1, rafts_per_slot + 1):
        if rid not in existing_ids:
            to_create.append({'day': date, 'slot': slot, 'raft_id': rid, 'occupancy': 0, 'is_special': False})
    if to_create:
        db.rafts.insert_many(to_create)

from utils.booking_ops import recompute_occupancy_for_slot
for d, s in dates_slots:
    if d and s:
        recompute_occupancy_for_slot(db, d, s)

print("Recompute complete.")
