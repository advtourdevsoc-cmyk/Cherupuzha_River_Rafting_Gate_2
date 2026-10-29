from pymongo import MongoClient
from config import MONGO_URI

client = MongoClient(MONGO_URI)
db = client.get_database('raft_booking')

# Check rafts for a specific date/slot
date_sample = '2026-04-19'
slot_sample = '1:00pm'

rafts = list(db.rafts.find({'day': date_sample, 'slot': slot_sample}))
print(f'Total rafts for {date_sample} {slot_sample}: {len(rafts)}')
print(f'First 5 rafts:')
for r in rafts[:5]:
    print(f'  Raft {r.get("raft_id")}: occupancy={r.get("occupancy")}')

settings = db.settings.find_one({'_id': 'system_settings'})
print(f'\nConfigured rafts_per_slot: {settings.get("rafts_per_slot")}')
print(f'Configured capacity: {settings.get("capacity")}')

# Calculate what the availability endpoint would compute
pipeline = [
    {
        '$match': {
            'day': date_sample,
            'slot': slot_sample
        }
    },
    {
        '$group': {
            '_id': '$slot',
            'total_occupancy': {'$sum': '$occupancy'},
            'has_special': {'$max': {'$cond': ['$is_special', 1, 0]}}
        }
    }
]

results = list(db.rafts.aggregate(pipeline))
if results:
    result = results[0]
    total_occ = result['total_occupancy']
    has_special = result['has_special']
    print(f'\nAggregation result:')
    print(f'  Total occupancy summed: {total_occ}')
    print(f'  Has special: {has_special}')
    
    # This is the logic from slot_availability
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)
    
    if total_occ == 0:
        available = rafts_per_slot * (capacity + 1)
    elif has_special:
        available = 0
    else:
        total_capacity = rafts_per_slot * capacity
        available = max(0, total_capacity - total_occ)
    
    print(f'\nAvailability calculation:')
    print(f'  Available: {available}')
    print(f'  Full: {available <= 0}')
    
    print(f'\nExpected calculation (using only 5 rafts):')
    actual_5_rafts = [r for r in rafts if r.get('raft_id') <= 5]
    print(f'  Actual rafts in use (raft_id 1-5): {len(actual_5_rafts)}')
    if actual_5_rafts:
        actual_occ = sum(r.get('occupancy', 0) for r in actual_5_rafts)
        print(f'  Actual occupancy (5 rafts only): {actual_occ}')
