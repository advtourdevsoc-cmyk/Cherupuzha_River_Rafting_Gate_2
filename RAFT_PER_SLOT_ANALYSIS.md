# Raft Per Slot Settings - Detailed Analysis

## Overview

The **"Rafts per Slot"** setting in the admin settings page is a critical configuration parameter that determines how many independent raft units are available for booking during each time slot throughout the day.

## Current Configuration

- **Setting Name**: `rafts_per_slot`
- **Default Value**: 5
- **Location**: `/admin/settings` → "Rafts per Slot" input field
- **Database Field**: `settings` collection, document `_id: 'system_settings'`
- **Data Type**: Integer (minimum 1)

## How It Works

### 1. Capacity Calculation

The `rafts_per_slot` setting directly impacts the total capacity available for a given time slot:

```
Normal Max Capacity per Slot = rafts_per_slot × capacity_per_raft
Bulk Max Capacity per Slot   = rafts_per_slot × (capacity_per_raft + 1)
```

**Example with Current Settings:**
- `rafts_per_slot = 5`
- `capacity = 6` (persons per raft)
- **Normal max per slot** = 5 × 6 = **30 people**
- **Bulk max per slot** = 5 × 7 = **35 people** (when slot is empty)

### 2. Raft Creation Per Date & Slot

When a date-slot combination is first accessed (booking or admin view), the system ensures the correct number of rafts exist:

**File**: `models/raft_model.py`

```python
def ensure_rafts_for_date_slot(db, date, slot, rafts_per_slot, capacity):
    existing = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1))
    if len(existing) >= rafts_per_slot:
        return
    # Create missing rafts numbered 1 to rafts_per_slot
    for rid in range(1, rafts_per_slot + 1):
        if rid not in existing_ids:
            db.rafts.insert_many({
                'day': date,
                'slot': slot,
                'raft_id': rid,
                'occupancy': 0,
                'is_special': False,
                'capacity': capacity
            })
```

### 3. Raft Structure in Database

Each raft document contains:

| Field | Example | Purpose |
|-------|---------|---------|
| `_id` | ObjectId | MongoDB document ID |
| `day` | "2026-04-20" | Date string (YYYY-MM-DD) |
| `slot` | "10:00am" | Time slot identifier |
| `raft_id` | 1, 2, 3, 4, 5 | Sequential raft number (1 to rafts_per_slot) |
| `occupancy` | 0-7 | Current number of people in the raft |
| `capacity` | 6 | Max persons for standard raft (7 for special) |
| `is_special` | False/True | Whether this is a 7-person (bulk) raft |

## Allocation Logic

**File**: `utils/allocation_logic.py`

The system uses sophisticated allocation patterns when assigning groups to rafts:

### Group Size Allocation Patterns:

```
1-3 people:    Try to merge into partially-filled existing raft
4-7 people:    Single raft allocation
8 people:      Split into [6, 2]
9 people:      Split into [6, 3]
10 people:     Split into [6, 4]
11+ people:    Smart distribution into 6/7 person rafts
```

**Example Allocation with 5 rafts × 6 capacity:**

- Group of 1-3 → Merge with existing if possible
- Group of 5 → Raft #1 (occupancy: 5)
- Group of 10 → Raft #2 (occupancy: 6) + Raft #3 (occupancy: 4)
- Group of 20 → Raft #2 (occupancy: 6) + Raft #3 (occupancy: 6) + Raft #4 (occupancy: 6) + Raft #5 (occupancy: 2)

## Settings Manager: Handling Changes

**File**: `utils/settings_manager.py`

When `rafts_per_slot` is changed in admin settings, the system automatically regenerates rafts:

### Change Detection Logic:

```python
old_rafts_per_slot = old_settings.get('rafts_per_slot', 5)  # e.g., 5
new_rafts_per_slot = new_settings.get('rafts_per_slot', 5)  # e.g., 7

if old_rafts_per_slot != new_rafts_per_slot:
    # Regenerate all rafts for all existing dates
    changes['rafts_regenerated'] = True
```

### Regeneration Process:

1. **Find all affected dates**:
   - All dates in the new booking range
   - All dates with existing rafts
   - All dates with existing bookings

2. **For each date-slot combination**:
   - Create new rafts up to `new_rafts_per_slot` count
   - If new count < old count, safely remove empty rafts
   - If a raft has occupancy > 0, keep it in database (won't display but data preserved)

3. **Notify admin**:
   - Flash message: "🔄 Rafts regenerated for all dates."

## Booking Flow & Validation

**File**: `routes/booking_routes.py`

### Capacity Checks:

```python
rafts_per_slot = settings.get('rafts_per_slot', 5)
capacity = settings.get('capacity', 6)

normal_max_people_per_slot = rafts_per_slot * capacity        # 30
bulk_max_people_per_slot = rafts_per_slot * (capacity + 1)    # 35

if group_size < 1 or group_size > bulk_max_people_per_slot:
    # Reject: exceeds maximum allowed for slot
    flash(f'Maximum allowed is {bulk_max_people_per_slot} people per slot')
```

### Date-Level Validation:

The system checks if an entire date is fully booked by:
1. Ensuring all rafts exist for all slots on that date
2. Calculating total vacancy across all slots
3. Rejecting bookings if all slots are full

## Admin Settings Page

**File**: `templates/settings.html` | **Route**: `routes/admin_routes.py`

### Form Field:
```html
<label class="block text-gray-700 font-semibold mb-2">Rafts per Slot</label>
<input type="number" name="rafts_per_slot" value="5" min="1" required>
```

### Validation (Backend):
- Minimum: 1
- Must be an integer
- Cannot be 0 or negative
- Changes trigger automatic raft regeneration

## Performance Implications

### Impact of Changing rafts_per_slot:

| Action | Impact | Cost |
|--------|--------|------|
| Increase from 5 to 7 | +2 extra rafts per slot per date | Creates ~240 new raft documents (7 slots × 30 days × 2 rafts) |
| Decrease from 7 to 5 | -2 rafts per slot per date (if empty) | Deletes only empty rafts; keeps occupied ones |
| Change triggers | All existing date-slots regenerated | Database writes proportional to: `dates × slots × difference` |

### Load on System:

- **Small bookings (1-3 people)**: Merge logic slightly slows down as more rafts exist to check
- **Normal bookings (4-10 people)**: Minimal impact, allocation is straightforward
- **Large bookings (20+ people)**: May use more rafts, but allocation is more efficient

## Real-World Scenarios

### Scenario 1: Summer Season Preparation
```
Current: rafts_per_slot = 5, capacity = 6 (30 people/slot)
Goal: Support larger groups
Change: Set rafts_per_slot = 10, capacity = 6 (60 people/slot)

Effect:
- 4 time slots × 30 booking days = 120 slot-date combinations
- Create 5 new rafts per combination = 600 new documents
- Admin sees: "✅ Settings updated successfully! | 🔄 Rafts regenerated for all dates."
```

### Scenario 2: Off-Season Optimization
```
Current: rafts_per_slot = 10, capacity = 6
Goal: Reduce operational complexity
Change: Set rafts_per_slot = 3, capacity = 6 (18 people/slot max)

Effect:
- 7 rafts removed per slot-date (if empty)
- Reduces total capacity from 60 to 18 per slot
- Admin receives warning in feedback if some rafts had occupancy > 0
```

### Scenario 3: Special Event
```
Current: rafts_per_slot = 5, time_slots = ['7:00am', '10:00am', '1:00pm', '3:30pm']
Goal: Add evening slot for event
Change: Add '6:00pm' to time_slots, keep rafts_per_slot = 5

Effect:
- New rafts created for existing dates with new '6:00pm' slot
- Doesn't affect existing slots
- New bookings can be made for the new slot immediately
```

## Relationship with Other Settings

### Capacity per Raft
- Works in tandem with `rafts_per_slot`
- Total slot capacity = `rafts_per_slot × capacity`
- Changing capacity updates occupancy limits for ALL existing rafts

### Time Slots
- Independent of `rafts_per_slot` value
- Each slot gets `rafts_per_slot` number of rafts
- Adding/removing slots triggers raft regeneration

### Booking Date Range (start_date / end_date)
- All dates in range receive `rafts_per_slot` rafts for each slot
- Changing range can trigger regeneration for new dates

## Monitoring & Debugging

### Check Current Rafts:
```python
from utils.allocation_logic import load_settings

settings = load_settings(db)
print(f'Current rafts_per_slot: {settings.get("rafts_per_slot")}')
print(f'Normal max per slot: {settings.get("normal_max_people_per_slot")}')  # rafts × capacity
print(f'Bulk max per slot: {settings.get("bulk_max_people_per_slot")}')      # rafts × (capacity+1)
```

### Count Rafts in Database:
```python
# Check how many rafts exist for a specific date-slot
rafts = db.rafts.find({'day': '2026-04-20', 'slot': '10:00am'})
print(f'Rafts for 2026-04-20 10:00am: {rafts.count()}')

# Check occupancy for all rafts in a slot
for raft in db.rafts.find({'day': '2026-04-20', 'slot': '10:00am'}):
    print(f"Raft {raft['raft_id']}: occupancy {raft['occupancy']}/{raft['capacity']}")
```

### Verify Raft Regeneration:
- Check database logs or Flask logs for messages like: `"🔄 Rafts regenerated for all dates."`
- Count rafts before/after: `db.rafts.count_documents({'slot': '10:00am'})`

## Key Takeaways

1. **Core Function**: Controls the number of independent raft units available per time slot
2. **Capacity Multiple**: Total slot capacity = `rafts_per_slot × capacity`
3. **Dynamic Regeneration**: Changing this value automatically recreates all rafts across all dates
4. **Booking Impact**: Directly determines max group sizes and slot availability
5. **Safe Changes**: System preserves data from occupied rafts even if count decreases
6. **Admin Control**: Accessible via `/admin/settings` form with real-time validation

---

## References

- **Settings Manager**: `utils/settings_manager.py`
- **Raft Model**: `models/raft_model.py`
- **Allocation Logic**: `utils/allocation_logic.py`
- **Booking Routes**: `routes/booking_routes.py` (capacity validation)
- **Admin Routes**: `routes/admin_routes.py` (settings form handler)
- **Settings Template**: `templates/settings.html`
