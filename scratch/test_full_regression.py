"""
Full regression test suite to ensure all core system functionalities remain unbroken.
"""
import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import unittest
from datetime import datetime, timezone, timedelta

# Import modules to test
from utils.amount_calculator import calculate_total_amount
from models.coupon_model import (
    create_coupon,
    get_active_coupon_by_phone,
    validate_coupon,
    delete_coupons_by_date_range,
    delete_single_coupon,
    is_coupon_expired
)

class MockDB:
    def __init__(self):
        self.coupons_data = []
        self.bookings_data = []

    @property
    def coupons(self):
        return MockCollection(self.coupons_data)

    @property
    def bookings(self):
        return MockCollection(self.bookings_data)

class MockCollection:
    def __init__(self, data_list):
        self.docs = data_list

    def find_one(self, query=None):
        for d in self.docs:
            match = True
            for k, v in query.items():
                if k == "$or":
                    or_match = False
                    for sub in v:
                        sub_match = all(d.get(sk) == sv for sk, sv in sub.items())
                        if sub_match:
                            or_match = True
                            break
                    if not or_match:
                        match = False
                        break
                elif d.get(k) != v:
                    match = False
                    break
            if match:
                return d
        return None

    def find(self, query=None, sort=None):
        if not query:
            res = list(self.docs)
        else:
            res = []
            for d in self.docs:
                match = True
                for k, v in query.items():
                    if k == "$or":
                        or_match = False
                        for sub in v:
                            sub_match = all(d.get(sk) == sv for sk, sv in sub.items())
                            if sub_match:
                                or_match = True
                                break
                        if not or_match:
                            match = False
                            break
                    elif k == "phone":
                        if d.get("phone") != v:
                            match = False
                    elif k == "is_used":
                        if d.get("is_used") != v:
                            match = False
                    elif k in ["is_revoked", "is_deleted"]:
                        if isinstance(v, dict) and "$ne" in v:
                            if d.get(k) == v["$ne"]:
                                match = False
                        elif d.get(k) != v:
                            match = False
                if match:
                    res.append(d)
        if sort:
            reverse = sort[0][1] == -1
            key_field = sort[0][0]
            res.sort(key=lambda x: x.get(key_field) or datetime.min.replace(tzinfo=timezone.utc), reverse=reverse)
        return res

    def insert_one(self, doc):
        doc['_id'] = f"id_{len(self.docs)+1}"
        self.docs.append(doc)
        class InsertRes:
            inserted_id = doc['_id']
        return InsertRes()

    def delete_many(self, query):
        in_ids = query.get("_id", {}).get("$in", [])
        to_remove = [d for d in self.docs if d.get("_id") in in_ids]
        count = len(to_remove)
        for d in to_remove:
            self.docs.remove(d)
        class Res:
            deleted_count = count
        return Res()

    def delete_one(self, query):
        doc = self.find_one(query)
        if doc and doc in self.docs:
            self.docs.remove(doc)
            class Res:
                deleted_count = 1
            return Res()
        class Res:
            deleted_count = 0
        return Res()


def test_amount_calculation():
    """Verify formula:
    Amount Per Person 1150
    Group size 13
    Advance Payment: 1150*13*0.25 = 3737.5
    Platform Fee: 3737.5 * 0.024 = 89.7
    Total Amount = (1150*13) + 89.7 = 15039.7
    Balance Amount = 15039.7 - 3737.5 = 11302.2
    """
    settings = {
        'weekday_amount': 1150,
        'weekday_advance_percent': 25.0,
        'platform_fee': 2.4
    }
    res = calculate_total_amount(
        settings=settings,
        booking_date_str="2026-09-29",
        group_size=13,
        amount_per_person=1150,
        coupon_discount=0
    )
    assert res['price_amount'] == 14950.0, f"Subtotal error: {res['price_amount']}"
    assert res['advance_amount'] == 3737.5, f"Advance payment error: {res['advance_amount']}"
    assert res['platform_fee'] == 89.7, f"Platform fee error: {res['platform_fee']}"
    assert res['total_amount'] == 15039.7, f"Total amount error: {res['total_amount']}"
    assert res['balance_amount'] == 11302.2, f"Balance amount error: {res['balance_amount']}"
    print("[OK] Test 1: Amount calculation formula passed!")


def test_coupon_group_size_condition():
    """Verify coupon applicability: requested group size >= stored group size."""
    db = MockDB()

    # Create coupon for group size 10
    c = create_coupon(
        db,
        phone="9876543210",
        discount_type="per_head",
        discount_value=100,
        created_by="admin",
        email="test@gmail.com",
        group_size=10
    )

    # 1. Group size 5 (5 < 10) -> Should NOT match
    active_5 = get_active_coupon_by_phone(db, "9876543210", email="test@gmail.com", group_size=5)
    assert active_5 is None, "Coupon should NOT be active for group size 5 < 10"

    v_coupon, v_err = validate_coupon(db, "9876543210", c['code'], email="test@gmail.com", group_size=5)
    assert v_coupon is None, "Validation should fail for group size 5 < 10"

    # 2. Group size 10 (10 == 10) -> Should match
    active_10 = get_active_coupon_by_phone(db, "9876543210", email="test@gmail.com", group_size=10)
    assert active_10 is not None, "Coupon SHOULD be active for group size 10 == 10"

    v_coupon10, v_err10 = validate_coupon(db, "9876543210", c['code'], email="test@gmail.com", group_size=10)
    assert v_coupon10 is not None, f"Validation failed: {v_err10}"

    # 3. Group size 15 (15 > 10) -> Should match
    active_15 = get_active_coupon_by_phone(db, "9876543210", email="test@gmail.com", group_size=15)
    assert active_15 is not None, "Coupon SHOULD be active for group size 15 > 10"

    print("[OK] Test 2: Coupon group size condition passed!")


def test_coupon_status_filtering_and_deletion():
    """Verify coupon date range deletion with status filtration."""
    db = MockDB()
    now = datetime.now(timezone.utc)
    today_str = now.strftime('%Y-%m-%d')
    valid_dt = now - timedelta(hours=1)
    expires_at = now + timedelta(hours=11)

    db.coupons_data = [
        {"_id": "c1", "code": "RAFT-VALID", "created_at": valid_dt, "expires_at": expires_at, "is_used": False, "is_revoked": False},
        {"_id": "c2", "code": "RAFT-USED", "created_at": valid_dt, "expires_at": expires_at, "is_used": True, "is_revoked": False},
        {"_id": "c3", "code": "RAFT-REVOKED", "created_at": valid_dt, "expires_at": expires_at, "is_used": False, "is_revoked": True},
    ]

    # Delete status 'Valid'
    success, count, msg = delete_coupons_by_date_range(db, today_str, today_str, status_filter="Valid")
    assert success is True
    assert count == 1, f"Expected 1 deleted coupon with status 'Valid', got {count}"
    assert len(db.coupons_data) == 2

    # Delete status 'all'
    success_all, count_all, msg_all = delete_coupons_by_date_range(db, today_str, today_str, status_filter="all")
    assert success_all is True
    assert count_all == 2, f"Expected 2 deleted coupons with status 'all', got {count_all}"
    assert len(db.coupons_data) == 0

    print("[OK] Test 3: Coupon status filtering and date range deletion passed!")


if __name__ == "__main__":
    print("Running Full Regression Test Suite...")
    test_amount_calculation()
    test_coupon_group_size_condition()
    test_coupon_status_filtering_and_deletion()
    print("\nALL REGRESSION TESTS PASSED SUCCESSFULLY!")
