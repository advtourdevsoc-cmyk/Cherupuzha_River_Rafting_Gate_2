"""
utils/effective_availability.py

Isolated helper module to calculate effective customer-facing available capacity.
This module is strictly read-only and does not perform raft allocation, seat reservation,
or database mutations.

The existing raft allocation logic remains completely untouched.
"""

from utils.allocation_logic import load_settings, get_allocation_pattern
from models.raft_model import ensure_rafts_for_date_slot


def is_group_size_allocatable(group_size, empty_count, partial_vacancies, capacity=6, rafts_per_slot=5):
    """
    Checks whether a candidate group_size can be accommodated by the current raft configuration
    (empty raft count and individual partial raft remaining vacancies), according to the project's
    allocation pattern rules.
    """
    if group_size <= 0:
        return False

    # Small groups (< 4) require merging into an existing partial raft
    if group_size < 4:
        return any(v >= group_size for v in partial_vacancies)

    max_per_slot = rafts_per_slot * (capacity + 1)
    allocation = get_allocation_pattern(group_size, max_per_slot)
    if not allocation:
        return False

    # Simulate placing each part in allocation into partial rafts first, then empty rafts
    temp_vacancies = list(partial_vacancies)
    unplaced_parts = []

    for part in allocation:
        placed = False
        # Try to merge into an existing partial raft (first fit)
        for idx, v in enumerate(temp_vacancies):
            if v >= part:
                temp_vacancies[idx] -= part
                placed = True
                break
        if not placed:
            unplaced_parts.append(part)

    # Each unplaced part requires 1 empty raft
    if len(unplaced_parts) <= empty_count:
        return True

    return False


def calculate_effective_available_capacity_from_rafts(rafts, rafts_per_slot=5, capacity=6):
    """
    Calculates the effective customer-facing maximum bookable group size for a slot,
    given the list of raft documents.
    
    Rules:
    1. Completely empty rafts (occupancy == 0) receive special condition treatment (capacity + 1 seats).
    2. Partially filled rafts (occupancy > 0) use actual remaining capacity (capacity - occupancy, or 7 - occupancy if is_special).
    3. Partial rafts ALWAYS remain separate and are NEVER combined into artificial rafts.
    4. If ALL rafts in the slot are empty, bulk booking mode allows up to rafts_per_slot * (capacity + 1).
    5. Otherwise, the effective maximum is the highest group size G that can actually be accommodated
       by the existing allocation patterns without exceeding individual raft capacities.
    """
    if not rafts or rafts_per_slot <= 0:
        return 0

    empty_count = 0
    partial_vacancies = []

    for r in rafts:
        occ = r.get('occupancy', 0)
        is_special = r.get('is_special', False)
        
        if occ == 0:
            empty_count += 1
        else:
            if is_special:
                rem = max(0, 7 - occ)
            else:
                rem = max(0, capacity - occ)
            if rem > 0:
                partial_vacancies.append(rem)

    total_rafts = len(rafts)
    max_bulk_capacity = rafts_per_slot * (capacity + 1)
    normal_max_capacity = rafts_per_slot * capacity

    # Rule 4: If ALL rafts in the slot are empty, bulk booking up to max_bulk_capacity is allowed.
    if empty_count == total_rafts and total_rafts == rafts_per_slot:
        return max_bulk_capacity

    empty_raft_capacity = capacity + 1
    physical_capacity = (empty_count * empty_raft_capacity) + sum(partial_vacancies)

    if physical_capacity <= 0:
        return 0

    # Max candidate group size cannot exceed normal_max_capacity (since not all rafts are empty)
    # or physical_capacity.
    max_candidate = min(normal_max_capacity, physical_capacity)

    # Search for highest group size G in [max_candidate ... 1] that is allocatable
    for G in range(max_candidate, 0, -1):
        if is_group_size_allocatable(G, empty_count, partial_vacancies, capacity, rafts_per_slot):
            return G

    return 0


def calculate_effective_available_capacity(db, date, slot):
    """
    Read-only helper to fetch rafts for a date and slot and return the customer-facing
    effective maximum available group size.
    """
    settings = load_settings(db)
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)

    ensure_rafts_for_date_slot(db, date, slot, rafts_per_slot, capacity)
    rafts = list(db.rafts.find({'day': date, 'slot': slot}).sort('raft_id', 1).limit(rafts_per_slot))

    return calculate_effective_available_capacity_from_rafts(rafts, rafts_per_slot, capacity)
