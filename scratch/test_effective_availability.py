import sys
import os

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.allocation_logic import get_allocation_pattern

def is_group_size_allocatable(group_size, empty_count, partial_vacancies, capacity=6, rafts_per_slot=5):
    if group_size <= 0:
        return False

    if group_size < 4:
        return any(v >= group_size for v in partial_vacancies)

    max_per_slot = rafts_per_slot * (capacity + 1)
    allocation = get_allocation_pattern(group_size, max_per_slot)
    if not allocation:
        return False

    temp_vacancies = list(partial_vacancies)
    unplaced_parts = []

    for part in allocation:
        placed = False
        for idx, v in enumerate(temp_vacancies):
            if v >= part:
                temp_vacancies[idx] -= part
                placed = True
                break
        if not placed:
            unplaced_parts.append(part)

    if len(unplaced_parts) <= empty_count:
        return True

    return False


def calculate_effective_available_capacity_from_rafts(rafts, rafts_per_slot=5, capacity=6):
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

    if empty_count == total_rafts and total_rafts == rafts_per_slot:
        return max_bulk_capacity

    empty_raft_capacity = capacity + 1
    physical_capacity = (empty_count * empty_raft_capacity) + sum(partial_vacancies)

    if physical_capacity <= 0:
        return 0

    max_candidate = min(normal_max_capacity, physical_capacity)

    for G in range(max_candidate, 0, -1):
        if is_group_size_allocatable(G, empty_count, partial_vacancies, capacity, rafts_per_slot):
            return G

    return 0


def run_tests():
    # Helper to construct raft list
    def make_rafts(empty_count, partial_vacancies, total_rafts=5, capacity=6):
        rafts = []
        for i in range(empty_count):
            rafts.append({'raft_id': i + 1, 'occupancy': 0, 'is_special': False})
        for v in partial_vacancies:
            rafts.append({'raft_id': len(rafts) + 1, 'occupancy': capacity - v, 'is_special': False})
        while len(rafts) < total_rafts:
            rafts.append({'raft_id': len(rafts) + 1, 'occupancy': capacity, 'is_special': False})
        return rafts

    test_cases = [
        # (empty_count, partial_vacancies, expected_eff_max)
        (5, [], 35),
        (4, [1], 28),
        (4, [2], 28),
        (4, [3], 28),
        (4, [4], 28),
        (4, [5], 29),
        (3, [1, 1], 21),
        (3, [1, 2], 21),
        (3, [1, 3], 21),
        (3, [1, 4], 22),
        (3, [1, 5], 23),
        (3, [2, 2], 21),
        (3, [2, 3], 21),
        (3, [2, 4], 22),
        (3, [2, 5], 23),
        (3, [3, 3], 21),
        (3, [3, 4], 22),
        (3, [3, 5], 23),
        (3, [4, 4], 22),
        (3, [4, 5], 23),
        (3, [5, 5], 23),
        # Two-empty scenarios
        (2, [1], 14),
        (2, [2], 14),
        (2, [3], 15),
        (2, [4], 16),
        (2, [5], 17),
        (2, [1, 1], 14),
        (2, [1, 2], 14),
        (2, [1, 3], 15),
        (2, [2, 2], 14),
        (2, [2, 3], 15),
        (2, [3, 3], 15),  # 15 can be allocated (6+6+3). 16 requires [6,6,4] which doesn't fit 3,3.
        (2, [4, 4], 16),
        (2, [5, 5], 17),
    ]

    passed = 0
    failed = 0
    for idx, (e_cnt, p_vacs, exp) in enumerate(test_cases, 1):
        r_list = make_rafts(e_cnt, p_vacs)
        res = calculate_effective_available_capacity_from_rafts(r_list)
        if res == exp:
            print(f"Test {idx:02d} PASSED: empty={e_cnt}, partials={p_vacs} => {res}")
            passed += 1
        else:
            print(f"Test {idx:02d} FAILED: empty={e_cnt}, partials={p_vacs} => GOT {res}, EXPECTED {exp}")
            failed += 1

    print(f"\nTotal: {passed + failed}, Passed: {passed}, Failed: {failed}")

if __name__ == '__main__':
    run_tests()
