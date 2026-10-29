# utils/allocation_logic.py
import math
from datetime import datetime, date, timedelta

def load_settings(db):
    settings = db.settings.find_one({'_id': 'system_settings'})
    if not settings:
        # Default settings with date range
        today = date.today()
        default_end = date(today.year, today.month, today.day) + timedelta(days=29)  # 30 days total
        settings = {
            'rafts_per_slot': 5,
            'capacity': 6,
            'time_slots': ['7:00am', '10:00am', '1:00pm', '3:30pm'],
            'start_date': today.isoformat(),
            'end_date': default_end.isoformat(),
            'days': 30
        }
    else:
        # If we have start_date and end_date, ensure days is calculated correctly
        if 'start_date' in settings and 'end_date' in settings:
            try:
                start = datetime.strptime(settings['start_date'], '%Y-%m-%d').date()
                end = datetime.strptime(settings['end_date'], '%Y-%m-%d').date()
                calculated_days = (end - start).days + 1
                # Update days if it doesn't match calculated value
                if settings.get('days') != calculated_days:
                    settings['days'] = calculated_days
            except (ValueError, TypeError):
                # If date parsing fails, keep existing days value
                pass
        # Backward compatibility: if no dates but has days, calculate dates from today
        elif 'days' in settings and 'start_date' not in settings:
            today = date.today()
            settings['start_date'] = today.isoformat()
            end_date = today + timedelta(days=settings['days'] - 1)
            settings['end_date'] = end_date.isoformat()
    
    # Calculate per-slot limits from rafts_per_slot and capacity.
    # - normal_max_people_per_slot: standard 6-person rafts, used for regular bookings
    # - bulk_max_people_per_slot: 7-person special mode, only when slot is completely empty
    rafts = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)
    settings['normal_max_people_per_slot'] = rafts * capacity
    settings['bulk_max_people_per_slot'] = rafts * (capacity + 1)
    return settings

# ---------- Allocation Pattern ----------
def get_allocation_pattern(people, max_per_slot):
    allocation = []
    if 4 <= people <= 7:
        allocation = [people]
    elif people == 8:
        allocation = [6, 2]
    elif people == 9:
        allocation = [6, 3]
    elif people == 10:
        allocation = [6, 4]
    elif 11 <= people <= max_per_slot:
        rafts_needed = math.ceil(people / 7.0)
        surplus = people - 6 * rafts_needed
        if surplus >= 0:
            allocation = [6 if i < rafts_needed - surplus else 7 for i in range(rafts_needed)]
        else:
            allocation = [6] * (rafts_needed - 1) + [6 + surplus]
    return allocation

def try_merge_into_existing_rafts(db, rafts, people, capacity):
    """Try to merge `people` into any partially filled, non-special raft.
    Returns raft_id if merged (and updates DB), else None.
    """
    for r in rafts:
        if not r.get('is_special', False) and r.get('occupancy', 0) > 0:
            vacancy = capacity - r['occupancy']
            if vacancy >= people:
                r['occupancy'] += people
                db.rafts.update_one({'_id': r['_id']}, {'$inc': {'occupancy': people}})
                return r['raft_id']
    return None

def allocate_raft(db, user_id, date, slot, group_size, allow_empty_for_small_group=True):
    """Implements C-style allocation:
    - small groups (<4) try merging into partial rafts, or (if allowed) allocate an empty raft
    - 4-7 single raft
    - 8..10 specific splits
    - >10 split into 6/7 patterns
    Returns {'status','rafts','message'}
    """
    settings = load_settings(db)
    capacity = settings['capacity']
    # Calculate max_people_per_slot dynamically: rafts_per_slot * (capacity + 1)
    # The +1 accounts for special 7-person rafts when capacity is 6
    max_people_per_slot = settings.get('rafts_per_slot', 5) * (capacity + 1)

    # Ensure rafts exist for this date/slot
    from models.raft_model import ensure_rafts_for_date_slot
    ensure_rafts_for_date_slot(db, date, slot, settings.get('rafts_per_slot', 5), capacity)
    
    # fetch rafts for date+slot sorted, limit to configured number
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    rafts = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1).limit(rafts_per_slot))
    if not rafts:
        return {'status': 'Pending', 'rafts': [], 'message': 'No rafts initialized for this slot.'}

    # ---------- Bulk booking logic ----------
    # A bulk booking is when group_size > (rafts_per_slot * capacity)
    # Allowed only if the slot is completely empty. In that case we allow
    # up to rafts_per_slot * (capacity + 1) people (special 7-person mode for each raft).
    if group_size > (rafts_per_slot * capacity):
        # Check whether all rafts in this slot are empty
        all_empty = all(r.get('occupancy', 0) == 0 for r in rafts)
        if not all_empty:
            return {'status': 'Pending', 'rafts': [], 'message': 'Pending – Bulk booking can only be done in an empty slot.'}

        # Compute the maximum allowed bulk booking for an empty slot
        max_bulk = rafts_per_slot * (capacity + 1)
        if group_size > max_bulk:
            return {'status': 'Pending', 'rafts': [], 'message': 'Not enough capacity in this slot.'}

        # Distribute people equally among all rafts, some rafts may get +1 to account for remainder
        base = group_size // rafts_per_slot
        rem = group_size % rafts_per_slot
        placed = []
        placement_details = []
        # Ensure deterministic ordering (already sorted above)
        for idx, r in enumerate(rafts):
            occ = base + (1 if idx < rem else 0)
            # Mark all rafts as special (7-capacity mode) and set occupancy
            db.rafts.update_one({'_id': r['_id']}, {'$set': {'occupancy': occ, 'is_special': True}})
            placed.append(r['raft_id'])
            placement_details.append({'raft_id': r['raft_id'], 'count': occ})

        return {'status': 'Confirmed', 'rafts': placed, 'raft_details': placement_details, 'message': f'Bulk allocated to rafts: {placed}'}


    # Initial capacity check: empty rafts can accommodate up to capacity + 1 (e.g. 7)
    total_vacancy = sum(
        capacity + 1 if r.get('occupancy', 0) == 0 else max(capacity - r.get('occupancy', 0), 0)
        for r in rafts
    )
    if total_vacancy < group_size:
        return {'status': 'Pending', 'rafts': [], 'message': 'Not enough capacity in this slot.'}

    # small groups (<4) - merge into any partially filled raft, or (if allowed) allocate an empty raft
    if group_size < 4:
        merged_raft = try_merge_into_existing_rafts(db, rafts, group_size, capacity)
        if merged_raft:
            return {'status': 'Confirmed', 'rafts': [merged_raft], 'raft_details': [{'raft_id': merged_raft, 'count': group_size}], 'message': f'Merged into Raft {merged_raft}'}
        
        if allow_empty_for_small_group:
            empty_rafts = [r for r in rafts if r.get('occupancy', 0) == 0]
            if empty_rafts:
                target = empty_rafts[0]
                target['occupancy'] = group_size
                target['is_special'] = False
                db.rafts.update_one({'_id': target['_id']}, {'$set': {'occupancy': group_size, 'is_special': False}})
                return {'status': 'Confirmed', 'rafts': [target['raft_id']], 'raft_details': [{'raft_id': target['raft_id'], 'count': group_size}], 'message': f'Allocated to Raft {target["raft_id"]}'}

        return {'status': 'Pending', 'rafts': [], 'message': 'Group size below minimum requirement of 4 people for an empty raft. Must merge into an existing partially filled raft.'}

    # compute allocation pattern as per C program
    allocation = get_allocation_pattern(group_size, max_people_per_slot)
    if not allocation:
        return {'status': 'Pending', 'rafts': [], 'message': 'Invalid group size or allocation pattern.'}

    # Try merging each allocation piece into existing rafts first
    placed = []
    placement_details = []
    for i, part in enumerate(list(allocation)):
        if part <= 0:
            continue
        merged = try_merge_into_existing_rafts(db, rafts, part, capacity)
        if merged:
            placed.append(merged)
            placement_details.append({'raft_id': merged, 'count': part})
            allocation[i] = 0  # mark placed

    # Collect unplaced parts
    unplaced = [p for p in allocation if p > 0]
    if not unplaced:
        return {'status': 'Confirmed', 'rafts': placed, 'raft_details': placement_details, 'message': f'All merged ({placed})'}

    # Find empty rafts
    empty_rafts = [r for r in rafts if r.get('occupancy', 0) == 0]
    if len(empty_rafts) < len(unplaced):
        return {'status': 'Pending', 'rafts': [], 'message': 'Not enough empty rafts available.'}

    # Allocate unplaced parts to empty rafts (mark is_special for 7)
    for idx, part in enumerate(unplaced):
        target = empty_rafts[idx]
        db.rafts.update_one({'_id': target['_id']}, {'$set': {'occupancy': part, 'is_special': (part == 7)}})
        placed.append(target['raft_id'])
        placement_details.append({'raft_id': target['raft_id'], 'count': part})

    return {'status': 'Confirmed', 'rafts': placed, 'raft_details': placement_details, 'message': f'Allocated to rafts: {placed}'}


def get_slots_for_date(db, date_str, settings=None):
    """
    Return all valid slots for a given date.
    Combines global settings.time_slots with any immediate/custom slots created for this date.
    """
    if settings is None:
        settings = load_settings(db)
    slots = list(settings.get('time_slots', []))
    if not date_str:
        return slots
    try:
        if db is not None:
            immediate = list(db.immediate_slots.find({'date': str(date_str).strip()}).sort('created_at', 1))
            for item in immediate:
                s = item.get('slot', '').strip()
                if s and s not in slots:
                    slots.append(s)
    except Exception:
        pass
    slots.sort(key=_parse_time_slot)
    return slots


def _parse_time_slot(slot_str):
    """Helper to convert slot strings like '7:00am', '1:00pm', '11:30 am' to minutes from midnight for sorting."""
    s = (slot_str or '').strip().lower().replace(' ', '')
    try:
        is_pm = 'pm' in s
        is_am = 'am' in s
        s_clean = s.replace('pm', '').replace('am', '')
        parts = s_clean.split(':')
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if is_pm and hour < 12:
            hour += 12
        elif is_am and hour == 12:
            hour = 0
        return hour * 60 + minute
    except Exception:
        return 9999


def get_slots_for_date_range(db, from_date=None, to_date=None, settings=None):
    """
    Return all valid slots for a given date range [from_date, to_date].
    Combines regular settings.time_slots with any immediate_slots configured
    for dates within [from_date, to_date], and any existing booking slots in that range.
    Slots are returned in natural chronological order.
    """
    if settings is None:
        settings = load_settings(db)

    base_slots = list(settings.get('time_slots', []))
    extra_slots = []

    from_date = (from_date or '').strip()
    to_date = (to_date or '').strip()

    # If no dates specified at all, return all active slots
    if not from_date and not to_date:
        return get_all_active_slots(db, settings)

    date_query = {}
    if from_date and to_date:
        if from_date == to_date:
            date_query = from_date
        else:
            date_query = {'$gte': from_date, '$lte': to_date}
    elif from_date:
        date_query = {'$gte': from_date}
    elif to_date:
        date_query = {'$lte': to_date}

    try:
        if db is not None:
            # Immediate slots in this date range
            imm_records = list(db.immediate_slots.find({'date': date_query}))
            for item in imm_records:
                s = (item.get('slot') or '').strip()
                if s and s not in base_slots and s not in extra_slots:
                    extra_slots.append(s)

            # Booking slots in this date range
            booking_slots = db.bookings.distinct('slot', {'date': date_query})
            for s in booking_slots:
                s_clean = (s or '').strip()
                if s_clean and s_clean not in base_slots and s_clean not in extra_slots:
                    extra_slots.append(s_clean)
    except Exception:
        pass

    all_combined = base_slots + [s for s in extra_slots if s not in base_slots]
    all_combined.sort(key=_parse_time_slot)
    return all_combined


def get_all_active_slots(db, settings=None):
    """
    Return all distinct slots across regular system settings, immediate_slots, and existing bookings.
    Ensures admin filter dropdowns show every possible slot that can be queried.
    """
    if settings is None:
        settings = load_settings(db)
    slots = list(settings.get('time_slots', []))
    try:
        if db is not None:
            extra = db.immediate_slots.distinct('slot')
            for s in extra:
                s_clean = (s or '').strip()
                if s_clean and s_clean not in slots:
                    slots.append(s_clean)
            booking_slots = db.bookings.distinct('slot')
            for s in booking_slots:
                s_clean = (s or '').strip()
                if s_clean and s_clean not in slots:
                    slots.append(s_clean)
    except Exception:
        pass
    slots.sort(key=_parse_time_slot)
    return slots



