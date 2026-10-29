"""
Test script to verify updated amount calculation logic.
"""
from utils.amount_calculator import calculate_total_amount

def test_user_example():
    # User example:
    # Amount per person: 1150
    # Group size: 13
    # Advance Payment %: 25% (0.25)
    # Platform Fee %: 2.4% (0.024)
    # Expected Advance Payment: 1150 * 13 * 0.25 = 3737.5
    # Expected Platform Fee: 3737.5 * 0.024 = 89.7
    # Expected Total Amount: (1150 * 13) + 89.7 = 15039.7
    # Expected Balance Amount: 15039.7 - 3737.5 = 11302.2

    settings = {
        'weekday_amount': 1150,
        'weekday_advance_percent': 25,
        'platform_fee': 2.4
    }

    # Test date: Monday (2026-10-05)
    result = calculate_total_amount(settings, "2026-10-05", 13)
    print("Test Result:", result)

    assert result['applicable_amount'] == 1150, f"Expected 1150, got {result['applicable_amount']}"
    assert result['price_amount'] == 14950.0, f"Expected 14950, got {result['price_amount']}"
    assert result['advance_amount'] == 3737.5, f"Expected 3737.5, got {result['advance_amount']}"
    assert result['platform_fee'] == 89.7, f"Expected 89.7, got {result['platform_fee']}"
    assert result['total_amount'] == 15039.7, f"Expected 15039.7, got {result['total_amount']}"
    assert result['balance_amount'] == 11302.2, f"Expected 11302.2, got {result['balance_amount']}"

    print("PASSED USER EXAMPLE TEST!")

def test_coupon_example():
    # Setting rate: 1300
    # Group size: 13
    # Coupon discount: 150 per head = 1950 total discount
    # Effective rate per person: 1150
    settings = {
        'weekday_amount': 1300,
        'weekday_advance_percent': 25,
        'platform_fee': 2.4
    }

    result = calculate_total_amount(settings, "2026-10-05", 13, amount_per_person=1300, coupon_discount=1950)
    print("Coupon Test Result:", result)

    assert result['price_amount'] == 14950.0, f"Expected 14950, got {result['price_amount']}"
    assert result['advance_amount'] == 3737.5, f"Expected 3737.5, got {result['advance_amount']}"
    assert result['platform_fee'] == 89.7, f"Expected 89.7, got {result['platform_fee']}"
    assert result['total_amount'] == 15039.7, f"Expected 15039.7, got {result['total_amount']}"
    assert result['balance_amount'] == 11302.2, f"Expected 11302.2, got {result['balance_amount']}"

    print("PASSED COUPON EXAMPLE TEST!")

if __name__ == "__main__":
    test_user_example()
    test_coupon_example()
