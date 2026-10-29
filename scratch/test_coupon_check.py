from datetime import datetime, timezone, timedelta

class DummyDB:
    def __init__(self, coupons):
        self.coupons_data = coupons
        self.coupons = self

    def find(self, query, sort=None):
        phone = query.get("phone")
        is_used = query.get("is_used")
        results = []
        for c in self.coupons_data:
            if c.get("phone") == phone and c.get("is_used") == is_used and not c.get("is_revoked") and not c.get("is_deleted"):
                results.append(c)
        return results

from models.coupon_model import get_active_coupon_by_phone

def test_coupon_matching():
    now = datetime.now(timezone.utc)
    sample_coupons = [
        {
            "code": "RAFT-1234",
            "phone": "9876543210",
            "email": "user@gmail.com",
            "group_size": 5,
            "is_used": False,
            "is_revoked": False,
            "is_deleted": False,
            "created_at": now,
            "expires_at": now + timedelta(hours=12)
        }
    ]
    db = DummyDB(sample_coupons)

    # 1. All match -> Should return coupon
    res1 = get_active_coupon_by_phone(db, "9876543210", email="user@gmail.com", group_size=5)
    assert res1 is not None and res1["code"] == "RAFT-1234", f"Expected RAFT-1234, got {res1}"
    print("Test 1 (All match): PASSED")

    # 2. Phone match, group size greater than generated size (e.g. 7 >= 5) -> Should return coupon
    res2_gt = get_active_coupon_by_phone(db, "9876543210", email="user@gmail.com", group_size=7)
    assert res2_gt is not None and res2_gt["code"] == "RAFT-1234", f"Expected RAFT-1234 for group_size 7 >= 5, got {res2_gt}"
    print("Test 2a (Group size 7 >= 5): PASSED")

    # 3. Phone match, group size less than generated size (e.g. 4 < 5) -> Should return None
    res2_lt = get_active_coupon_by_phone(db, "9876543210", email="user@gmail.com", group_size=4)
    assert res2_lt is None, f"Expected None for group_size 4 < 5, got {res2_lt}"
    print("Test 2b (Group size 4 < 5): PASSED")

    # 4. Phone match, email mismatch -> Should return None
    res3 = get_active_coupon_by_phone(db, "9876543210", email="other@gmail.com", group_size=5)
    assert res3 is None, f"Expected None for email mismatch, got {res3}"
    print("Test 3 (Email mismatch): PASSED")

    # 5. Phone mismatch -> Should return None
    res4 = get_active_coupon_by_phone(db, "1111111111", email="user@gmail.com", group_size=5)
    assert res4 is None, f"Expected None for phone mismatch, got {res4}"
    print("Test 4 (Phone mismatch): PASSED")

if __name__ == "__main__":
    test_coupon_matching()
