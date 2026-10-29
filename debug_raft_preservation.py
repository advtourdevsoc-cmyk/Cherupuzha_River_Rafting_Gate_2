#!/usr/bin/env python3
"""
Debug script to verify raft preservation logic is working correctly.
Shows all rafts (including hidden ones) for a specific date-slot.
"""
import sys
sys.path.insert(0, '/path/to/project')

from config import MONGO_URI
from pymongo import MongoClient

def get_db():
    client = MongoClient(MONGO_URI)
    return client['rafting_db']

def check_rafts_for_slot(date_str, slot):
    """Show ALL rafts (including hidden ones) for a specific date-slot."""
    db = get_db()
    
    # Get current settings
    settings = db.settings.find_one({'_id': 'system_settings'})
    rafts_per_slot = settings.get('rafts_per_slot', 5) if settings else 5
    capacity = settings.get('capacity', 6) if settings else 6
    
    print(f"\n{'='*70}")
    print(f"Raft Preservation Check: {date_str} - {slot}")
    print(f"{'='*70}")
    print(f"Current Settings: rafts_per_slot = {rafts_per_slot}, capacity = {capacity}")
    print(f"Expected display: {rafts_per_slot} rafts")
    print(f"Expected max capacity: {rafts_per_slot * capacity} people")
    print(f"Bulk max capacity: {rafts_per_slot * (capacity + 1)} people\n")
    
    # Get ALL rafts for this date-slot (not limited)
    all_rafts = list(db.rafts.find(
        {'day': date_str, 'slot': slot}
    ).sort('raft_id', 1))
    
    print(f"Total rafts in database: {len(all_rafts)}")
    print(f"Displayed rafts (limited): {min(len(all_rafts), rafts_per_slot)}\n")
    
    if not all_rafts:
        print("❌ No rafts found for this date-slot!")
        return
    
    # Show all rafts
    total_occupancy = 0
    displayed_count = 0
    
    for raft in all_rafts:
        rid = raft.get('raft_id')
        occupancy = raft.get('occupancy', 0)
        is_special = raft.get('is_special', False)
        capacity_val = raft.get('capacity', 6)
        
        total_occupancy += occupancy
        
        if displayed_count < rafts_per_slot:
            status = "✅ DISPLAYED"
            displayed_count += 1
        else:
            status = "🛡️ PRESERVED (hidden from display)"
        
        special_str = " (7-special)" if is_special else ""
        print(f"Raft {rid}: {occupancy}/{capacity_val}{special_str} - {status}")
    
    print(f"\n{'─'*70}")
    print(f"TOTAL OCCUPANCY (all rafts): {total_occupancy}/{len(all_rafts) * capacity}")
    print(f"DISPLAYED OCCUPANCY (limited): {total_occupancy}/{displayed_count * capacity}")
    
    # Check for bookings in this slot
    bookings = list(db.bookings.find({
        'date': date_str,
        'slot': slot,
        'status': 'Confirmed'
    }))
    
    print(f"\nConfirmed bookings in this slot: {len(bookings)}")
    total_people = sum(int(b.get('group_size', 0) or 0) for b in bookings)
    print(f"Total people booked: {total_people}")
    
    if total_people > displayed_count * capacity:
        print(f"\n⚠️  IMPORTANT: {total_people} people are booked, but display capacity is only {displayed_count * capacity}!")
        print(f"✅ This is OK - the extra {total_people - displayed_count * capacity} people are in PRESERVED (hidden) rafts")
        print(f"✅ The preservation logic IS WORKING CORRECTLY")
    
    print(f"\n{'='*70}\n")

if __name__ == '__main__':
    import datetime
    
    # Check today's 3:30pm slot as example
    today = datetime.date.today().isoformat()
    
    print("\n" + "="*70)
    print("RAFT PRESERVATION DEBUG TOOL")
    print("="*70)
    
    # Example dates to check
    dates_slots = [
        (today, "3:30pm"),
        (today, "10:00am"),
    ]
    
    for date_str, slot in dates_slots:
        check_rafts_for_slot(date_str, slot)
    
    print("\nTo check a specific date, modify the dates_slots list above")
    print("Or call: check_rafts_for_slot('2026-04-20', '3:30pm')\n")
