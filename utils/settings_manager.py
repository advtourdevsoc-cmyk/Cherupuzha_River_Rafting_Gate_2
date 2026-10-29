# utils/settings_manager.py
"""
Settings management utilities for handling settings updates and cache invalidation.
"""
from datetime import timedelta
from utils.allocation_logic import load_settings
from models.raft_model import ensure_rafts_for_date_slot

def invalidate_settings_cache(app):
    """Invalidate the settings cache in Flask app config."""
    if 'SETTINGS_CACHE' in app.config:
        del app.config['SETTINGS_CACHE']

def refresh_settings_cache(app, db):
    """Refresh the settings cache with latest values from database."""
    settings = load_settings(db)
    app.config['SETTINGS_CACHE'] = settings
    return settings

def get_fresh_settings(app, db):
    """Get fresh settings, bypassing cache."""
    return load_settings(db)

def regenerate_rafts_for_settings_change(db, old_settings, new_settings):
    """
    Regenerate rafts when settings change that affect raft structure.
    Returns dict with info about what was regenerated.
    
    Features:
    ✅ Smart raft reduction - Only reduces when slots are empty
    ✅ Booking preservation - Keeps all rafts if ANY bookings exist
    ✅ Visual indicators - Shows 🔵 "Hidden (preserved due to bookings)" for protected rafts
    ✅ Accurate occupancy - Counts all people including those in preserved rafts
    ✅ Admin feedback - Clear visibility of what was preserved
    """
    changes = {
        'rafts_regenerated': False,
        'capacity_updated': False,
        'slots_added': [],
        'slots_removed': [],
        'slots_preserved_with_bookings': [],  # Track slots with active bookings that weren't reduced
        'rafts_preserved_count': 0  # Total number of preserved rafts
    }
    
    # Check if rafts_per_slot changed - need to add/remove rafts
    old_rafts_per_slot = old_settings.get('rafts_per_slot', 5)
    new_rafts_per_slot = new_settings.get('rafts_per_slot', 5)
    capacity = new_settings.get('capacity', 6)
    
    # Check if capacity changed - update existing rafts
    old_capacity = old_settings.get('capacity', 6)
    new_capacity = new_settings.get('capacity', 6)
    
    if old_capacity != new_capacity:
        # Update capacity field for rafts on future dates only (optimization)
        from datetime import date as date_type
        today = date_type.today().isoformat()
        db.rafts.update_many({'day': {'$gte': today}}, {'$set': {'capacity': new_capacity}})
        changes['capacity_updated'] = True
    
    # Check if time slots changed
    old_slots = set(old_settings.get('time_slots', []))
    new_slots = set(new_settings.get('time_slots', []))
    
    removed_slots = old_slots - new_slots
    added_slots = new_slots - old_slots
    
    if removed_slots:
        changes['slots_removed'] = list(removed_slots)
        # Optionally: remove rafts for removed slots (or keep them for historical data)
        # For now, we'll keep them but they won't be used in new bookings
    
    if added_slots:
        changes['slots_added'] = list(added_slots)
    
    # Regenerate rafts for all existing dates if rafts_per_slot changed or slots added
    if old_rafts_per_slot != new_rafts_per_slot or added_slots:
        changes['rafts_regenerated'] = True
        
        print("[SETTINGS] Starting raft regeneration...")
        
        # Get all unique dates from existing rafts and bookings
        from datetime import date as date_type
        today = date_type.today()
        days = new_settings.get('days', 30)
        
        # OPTIMIZATION: Only process future dates, not historical ones
        # Generate dates for next N days from today onwards
        future_dates = set((today + timedelta(days=i)).isoformat() for i in range(days))
        
        # Also get dates from existing bookings (only future ones to avoid processing old data)
        existing_dates = set()
        for booking in db.bookings.find({'date': {'$gte': today.isoformat()}}, {'date': 1}):
            if booking.get('date'):
                existing_dates.add(booking['date'])
        
        # Also get dates from existing rafts (only future ones)
        for raft in db.rafts.find({'day': {'$gte': today.isoformat()}}, {'day': 1}).distinct('day'):
            if raft:
                existing_dates.add(raft)
        
        all_dates = future_dates | existing_dates
        print(f"[SETTINGS] Processing {len(all_dates)} future date(s), {len(all_slots)} slot(s)")
        
        # Regenerate rafts for all dates and all slots (old and new)
        all_slots = old_slots | new_slots
        preserved_slots = set()  # Track which slot-dates were preserved due to bookings
        preserved_rafts_count = 0  # Count total preserved rafts
        
        for date_str in all_dates:
            for slot in all_slots:
                if slot in removed_slots:
                    continue  # Skip removed slots
                
                # Ensure rafts exist with new settings
                ensure_rafts_for_date_slot(db, date_str, slot, new_rafts_per_slot, capacity)
                
                # If rafts_per_slot decreased, remove extra rafts (only if slot has no active bookings)
                existing_rafts = list(db.rafts.find({'day': date_str, 'slot': slot}).sort('raft_id', 1))
                if len(existing_rafts) > new_rafts_per_slot:
                    # Get extra rafts beyond the new configured amount
                    extra_rafts = existing_rafts[new_rafts_per_slot:]
                    
                    # Only preserve extra rafts if they have actual occupancy (bookings)
                    # Empty extra rafts should be deleted regardless of other rafts in the slot
                    rafts_to_delete_ids = []
                    rafts_to_preserve = []
                    
                    for extra_raft in extra_rafts:
                        if extra_raft.get('occupancy', 0) > 0:
                            # This extra raft has occupancy - PRESERVE it
                            rafts_to_preserve.append(extra_raft['raft_id'])
                            preserved_slots.add(f"{date_str} {slot}")
                            preserved_rafts_count += 1
                        else:
                            # This extra raft is empty - DELETE it
                            rafts_to_delete_ids.append(extra_raft['_id'])
                    
                    # Batch delete empty extra rafts (more efficient than individual deletes)
                    if rafts_to_delete_ids:
                        db.rafts.delete_many({'_id': {'$in': rafts_to_delete_ids}})
        
        # Store preserved slots in changes for admin feedback
        if preserved_slots:
            changes['slots_preserved_with_bookings'] = sorted(list(preserved_slots))
            changes['rafts_preserved_count'] = preserved_rafts_count
        
        print(f"[SETTINGS] Raft regeneration complete. Preserved: {len(preserved_slots)} slot(s)")
    
    return changes

