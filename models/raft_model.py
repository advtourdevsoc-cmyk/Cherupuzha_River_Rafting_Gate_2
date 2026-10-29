from pymongo.errors import DuplicateKeyError

def ensure_rafts_for_date_slot(db, date, slot, rafts_per_slot, capacity):
    existing = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1))
    
    # Deduplicate if duplicates are detected
    seen_ids = set()
    for r in existing:
        rid = r.get('raft_id')
        if rid in seen_ids:
            try:
                db.rafts.delete_one({'_id': r['_id']})
            except Exception:
                pass
        else:
            seen_ids.add(rid)

    for rid in range(1, rafts_per_slot + 1):
        if rid not in seen_ids:
            try:
                db.rafts.update_one(
                    {'day': date, 'slot': slot, 'raft_id': rid},
                    {'$setOnInsert': {
                        'day': date,
                        'slot': slot,
                        'raft_id': rid,
                        'occupancy': 0,
                        'is_special': False,
                        'capacity': capacity
                    }},
                    upsert=True
                )
            except DuplicateKeyError:
                pass

def ensure_rafts_for_dates_and_slots(db, allowed_dates, slots, rafts_per_slot, capacity):
    # Fetch all existing rafts for these dates and slots
    existing = list(db.rafts.find({
        'day': {'$in': allowed_dates},
        'slot': {'$in': slots}
    }))
    
    # Organize existing explicitly and remove duplicates
    existing_map = {}
    for r in existing:
        key = (r.get('day'), r.get('slot'))
        if key not in existing_map:
            existing_map[key] = set()
        rid = r.get('raft_id')
        if rid in existing_map[key]:
            try:
                db.rafts.delete_one({'_id': r['_id']})
            except Exception:
                pass
        else:
            existing_map[key].add(rid)
        
    for date in allowed_dates:
        for slot in slots:
            key = (date, slot)
            existing_ids = existing_map.get(key, set())
            if len(existing_ids) < rafts_per_slot:
                for rid in range(1, rafts_per_slot + 1):
                    if rid not in existing_ids:
                        try:
                            db.rafts.update_one(
                                {'day': date, 'slot': slot, 'raft_id': rid},
                                {'$setOnInsert': {
                                    'day': date, 
                                    'slot': slot, 
                                    'raft_id': rid, 
                                    'occupancy': 0, 
                                    'is_special': False, 
                                    'capacity': capacity
                                }},
                                upsert=True
                            )
                        except DuplicateKeyError:
                            pass

