#!/usr/bin/env python3
"""
Clean up test bookings and verify 7:00am slot is empty.
"""
import sys
from datetime import date, timedelta
from pymongo import MongoClient
from config import MONGO_URI

sys.path.insert(0, '.')

from utils.allocation_logic import load_settings
from models.raft_model import ensure_rafts_for_date_slot
from utils.booking_ops import recompute_occupancy_for_slot


def main():
    client = MongoClient(MONGO_URI)
    db = client.get_database()
    
    settings = load_settings(db)
    slots = settings.get('time_slots', [])
    slot = slots[0]  # 7:00am
    
    # Test date from admin panel
    day = '2026-02-23'
    
    print(f"Cleaning up test data for {day} {slot}\n")
    
    # Show current state
    print("Before cleanup:")
    rafts_before = list(db.rafts.find({'day': day, 'slot': slot}).sort('raft_id', 1))
    total_before = sum(r.get('occupancy', 0) for r in rafts_before)
    for r in rafts_before:
        print(f"  Raft {r['raft_id']}: {r.get('occupancy', 0)}/6")
    print(f"  TOTAL: {total_before}/30\n")
    
    # Show bookings
    print("Bookings in this slot:")
    bookings = list(db.bookings.find({'date': day, 'slot': slot}))
    for b in bookings:
        print(f"  {b.get('group_size')} people - Status: {b.get('status')} - ID: {b['_id']}")
    
    # Delete test bookings (pick by email or date range)
    print(f"\nDeleting bookings for {day}...")
    result = db.bookings.delete_many({'date': day, 'slot': slot})
    print(f"  Deleted: {result.deleted_count} bookings")
    
    # Reset raft occupancies
    print(f"\nResetting raft occupancies...")
    db.rafts.update_many({'day': day, 'slot': slot}, {'$set': {'occupancy': 0, 'is_special': False}})
    
    # Recompute to verify
    print(f"\nRecomputing occupancy for {day} {slot}...")
    recompute_occupancy_for_slot(db, day, slot)
    
    # Show final state
    print("\nAfter cleanup:")
    rafts_after = list(db.rafts.find({'day': day, 'slot': slot}).sort('raft_id', 1))
    total_after = sum(r.get('occupancy', 0) for r in rafts_after)
    for r in rafts_after:
        print(f"  Raft {r['raft_id']}: {r.get('occupancy', 0)}/6")
    print(f"  TOTAL: {total_after}/30\n")
    
    if total_after == 0:
        print("✅ Slot cleaned! Should now show 0/30 in admin panel.")
        return 0
    else:
        print(f"⚠️  Slot still has {total_after}, expected 0")
        return 1


if __name__ == '__main__':
    sys.exit(main())
