"""
Comprehensive end-to-end test script to verify that the updated coupon logic and amount calculation
do not break any part of the booking flow.
"""
from datetime import datetime, timezone, timedelta
from utils.amount_calculator import calculate_total_amount
from models.coupon_model import get_active_coupon_by_phone, validate_coupon

class MockCollection:
    def __init__(self):
        self.docs = []

    def find_one(self, query):
        code = query.get("code")
        for d in self.docs:
            if code and d.get("code") == code:
                return d
        return None

    def find(self, query, sort=None):
        phone = query.get("phone")
        is_used = query.get("is_used")
        results = []
        for d in self.docs:
            if d.get("phone") == phone and d.get("is_used") == is_used:
                results.append(d)
        return results

class MockDB:
    def __init__(self):
        self.coupons = MockCollection()

def test_booking_flow_with_coupon_larger_group_size():
    # Scenario: Admin generated a coupon for group size = 5 with 150/head discount.
    # Customer books for group size = 13 (13 >= 5).
    now = datetime.now(timezone.utc)
    db = MockDB()
    coupon_doc = {
        "code": "RAFT-TEST13",
        "phone": "9876543210",
        "email": "customer@gmail.com",
        "group_size": 5,
        "discount_type": "per_head",
        "discount_value": 150.0,
        "is_used": False,
        "is_revoked": False,
        "is_deleted": False,
        "created_at": now,
        "expires_at": now + timedelta(hours=12)
    }
    db.coupons.docs.append(coupon_doc)

    # Step 1: Check coupon availability on form entry (group_size = 13)
    c_active = get_active_coupon_by_phone(db, "9876543210", email="customer@gmail.com", group_size=13)
    assert c_active is not None, "Active coupon should be found for group_size 13 >= 5"
    assert c_active['code'] == "RAFT-TEST13"

    # Step 2: Validate coupon during form submission
    coupon_validated, err = validate_coupon(db, "9876543210", "RAFT-TEST13", email="customer@gmail.com", group_size=13)
    assert err is None, f"Validation should succeed, but got error: {err}"
    assert coupon_validated is not None

    # Step 3: Calculate booking amounts
    settings = {
        'weekday_amount': 1300,
        'weekday_advance_percent': 25,
        'platform_fee': 2.4
    }
    booking_date_str = "2026-10-05" # Monday
    group_size = 13
    amount_per_person = 1300
    setting_price_amount = 1300 * 13 # 16900

    dval = float(coupon_validated['discount_value']) # 150
    per_head_discount = min(dval, amount_per_person) # 150
    discount_applied = round(per_head_discount * group_size, 2) # 1950.0

    calc = calculate_total_amount(
        settings,
        booking_date_str,
        group_size,
        amount_per_person=amount_per_person,
        coupon_discount=discount_applied
    )

    print("Booking Calculation Output:", calc)

    # Verify expected numbers according to user formula:
    # Effective rate: 1150
    # Subtotal: 14950
    # Advance Payment: 3737.5
    # Platform Fee: 89.7
    # Total Amount: 15039.7
    # Balance Amount: 11302.2
    assert calc['price_amount'] == 14950.0
    assert calc['advance_amount'] == 3737.5
    assert calc['platform_fee'] == 89.7
    assert calc['total_amount'] == 15039.7
    assert calc['balance_amount'] == 11302.2

    print("END-TO-END BOOKING FLOW TEST WITH LARGER GROUP SIZE PASSED!")

def test_booking_flow_with_group_size_smaller_than_generated():
    # Scenario: Coupon generated for group size = 5. Customer tries booking for group size = 3 (< 5).
    now = datetime.now(timezone.utc)
    db = MockDB()
    coupon_doc = {
        "code": "RAFT-TEST5",
        "phone": "9876543210",
        "email": "customer@gmail.com",
        "group_size": 5,
        "discount_type": "per_head",
        "discount_value": 100.0,
        "is_used": False,
        "is_revoked": False,
        "is_deleted": False,
        "created_at": now,
        "expires_at": now + timedelta(hours=12)
    }
    db.coupons.docs.append(coupon_doc)

    # Active check should return None for group_size 3 < 5
    c_active = get_active_coupon_by_phone(db, "9876543210", email="customer@gmail.com", group_size=3)
    assert c_active is None, "Active coupon should NOT be found for group_size 3 < 5"

    # Validation should fail with clear message
    _, err = validate_coupon(db, "9876543210", "RAFT-TEST5", email="customer@gmail.com", group_size=3)
    assert err == "Group size must be at least 5 to use this coupon.", f"Unexpected error msg: {err}"

    print("END-TO-END TEST FOR GROUP SIZE SMALLER THAN GENERATED PASSED!")

if __name__ == "__main__":
    test_booking_flow_with_coupon_larger_group_size()
    test_booking_flow_with_group_size_smaller_than_generated()
