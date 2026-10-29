#!/usr/bin/env python3
"""
Quick verification script to check preserved rafts in database
"""
import sys
from config import MONGO_URI
from pymongo import MongoClient
from datetime import date, timedelta

client = MongoClient(MONGO_URI)
db = client['rafting_db']

# Get current settings
settings = db.settings.find_one({'_id': 'system_settings'})
rafts_per_slot = settings.get('rafts_per_slot', 5) if settings else 5
print(f"Current rafts_per_slot: {rafts_per_slot}")

# Check a specific date (today)
today = date.today().isoformat()
print(f"\nChecking date: {today}\n")

# Get all rafts for today
all_rafts = list(db.rafts.find({'day': today}).sort('raft_id', 1))
print(f"Total rafts in database for today: {len(all_rafts)}")

# Group by slot
by_slot = {}
for raft in all_rafts:
    slot = raft.get('slot')
    if slot not in by_slot:
        by_slot[slot] = []
    by_slot[slot].append(raft)

# Show each slot
for slot in ['10:00am', '1:00pm', '3:30pm', '7:00am']:
    if slot in by_slot:
        rafts = by_slot[slot]
        print(f"\n{slot}: {len(rafts)} rafts in database")
        for r in rafts:
            occupancy = r.get('occupancy', 0)
            is_special = r.get('is_special', False)
            special_str = " (7-special)" if is_special else ""
            print(f"  Raft {r.get('raft_id')}: occupancy={occupancy}{special_str}")
        
        # Check if any have occupancy
        has_occupancy = any(r.get('occupancy', 0) > 0 for r in rafts)
        print(f"  Has any bookings: {has_occupancy}")

print("\n" + "="*50)
print("✅ If you see more than 4 rafts in a slot with occupancy > 0")
print("   then preservation is working and display just needs refresh")
print("❌ If you only see 4 rafts, the preservation logic didn't work")
