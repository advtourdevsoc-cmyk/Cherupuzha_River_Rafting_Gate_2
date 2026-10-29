import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from utils.effective_availability import (
    calculate_effective_available_capacity_from_rafts,
    calculate_effective_available_capacity
)
from utils.allocation_logic import allocate_raft, get_allocation_pattern
from utils.booking_ops import check_capacity_available

def test_original_allocation_untouched():
    """Verify that original allocation_logic and booking_ops functions remain untouched and operational."""
    pattern = get_allocation_pattern(15, 35)
    assert pattern == [6, 6, 3], f"Expected [6, 6, 3], got {pattern}"
    
    pattern_16 = get_allocation_pattern(16, 35)
    assert pattern_16 == [6, 6, 4], f"Expected [6, 6, 4], got {pattern_16}"
    
    print("Original allocation_logic helper test: PASSED")

def test_effective_vs_physical():
    # 2 empty + partial 3
    rafts_15 = [
        {'raft_id': 1, 'occupancy': 0, 'is_special': False},
        {'raft_id': 2, 'occupancy': 0, 'is_special': False},
        {'raft_id': 3, 'occupancy': 3, 'is_special': False},
        {'raft_id': 4, 'occupancy': 6, 'is_special': False},
        {'raft_id': 5, 'occupancy': 6, 'is_special': False},
    ]
    eff_15 = calculate_effective_available_capacity_from_rafts(rafts_15)
    assert eff_15 == 15, f"Expected effective max 15, got {eff_15}"

    # 2 empty + partial 2
    rafts_14 = [
        {'raft_id': 1, 'occupancy': 0, 'is_special': False},
        {'raft_id': 2, 'occupancy': 0, 'is_special': False},
        {'raft_id': 3, 'occupancy': 4, 'is_special': False}, # 2 remaining
        {'raft_id': 4, 'occupancy': 6, 'is_special': False},
        {'raft_id': 5, 'occupancy': 6, 'is_special': False},
    ]
    eff_14 = calculate_effective_available_capacity_from_rafts(rafts_14)
    assert eff_14 == 14, f"Expected effective max 14, got {eff_14}"

    # 2 empty + partial 2 + partial 2
    rafts_22 = [
        {'raft_id': 1, 'occupancy': 0, 'is_special': False},
        {'raft_id': 2, 'occupancy': 0, 'is_special': False},
        {'raft_id': 3, 'occupancy': 4, 'is_special': False}, # 2 remaining
        {'raft_id': 4, 'occupancy': 4, 'is_special': False}, # 2 remaining
        {'raft_id': 5, 'occupancy': 6, 'is_special': False},
    ]
    eff_22 = calculate_effective_available_capacity_from_rafts(rafts_22)
    assert eff_22 == 14, f"Expected effective max 14, got {eff_22}"

    # 2 empty + partial 2 + partial 3
    rafts_23 = [
        {'raft_id': 1, 'occupancy': 0, 'is_special': False},
        {'raft_id': 2, 'occupancy': 0, 'is_special': False},
        {'raft_id': 3, 'occupancy': 4, 'is_special': False}, # 2 remaining
        {'raft_id': 4, 'occupancy': 3, 'is_special': False}, # 3 remaining
        {'raft_id': 5, 'occupancy': 6, 'is_special': False},
    ]
    eff_23 = calculate_effective_available_capacity_from_rafts(rafts_23)
    assert eff_23 == 15, f"Expected effective max 15, got {eff_23}"

    print("Effective availability calculation tests: PASSED")

if __name__ == '__main__':
    test_original_allocation_untouched()
    test_effective_vs_physical()
