"""
Unit test script to verify coupon date range deletion, status filtration, and single coupon deletion.
"""
from datetime import datetime, timezone, timedelta
from models.coupon_model import delete_coupons_by_date_range, delete_single_coupon

class MockCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(doc)

    def find(self, query=None, sort=None):
        return self.docs

    def delete_many(self, query):
        in_ids = query.get("_id", {}).get("$in", [])

        to_remove = []
        for d in self.docs:
            if in_ids and d.get("_id") in in_ids:
                to_remove.append(d)

        count = len(to_remove)
        for d in to_remove:
            if d in self.docs:
                self.docs.remove(d)

        class Result:
            pass
        res = Result()
        res.deleted_count = count
        return res

    def delete_one(self, query):
        cid = query.get("_id")
        code = query.get("code")
        to_remove = None
        for d in self.docs:
            if (cid and str(d.get("_id")) == str(cid)) or (code and d.get("code") == code):
                to_remove = d
                break
        count = 0
        if to_remove:
            self.docs.remove(to_remove)
            count = 1
        class Result:
            pass
        res = Result()
        res.deleted_count = count
        return res

class MockDB:
    def __init__(self):
        self.coupons = MockCollection()

def test_date_range_and_status_deletion():
    db = MockDB()
    now = datetime.now(timezone.utc)
    sep1 = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    sep15 = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)
    sep28 = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)
    oct5 = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)

    db.coupons.docs = [
        {"_id": "c1", "code": "RAFT-SEP01", "created_at": sep1, "is_used": False, "is_revoked": False},
        {"_id": "c2", "code": "RAFT-SEP15", "created_at": sep15, "is_used": True, "is_revoked": False},
        {"_id": "c3", "code": "RAFT-SEP28", "created_at": sep28, "is_used": False, "is_revoked": True},
        {"_id": "c4", "code": "RAFT-OCT05", "created_at": oct5, "is_used": False, "is_revoked": False},
    ]

    # Delete with status filter 'Used': 2026-09-01 to 2026-09-30 -> Should delete c2 only
    success, count, msg = delete_coupons_by_date_range(db, "2026-09-01", "2026-09-30", status_filter="Used")
    print("Delete Used Status Result:", success, count, msg)
    assert success is True
    assert count == 1, f"Expected 1 deleted coupon, got {count}"
    assert len(db.coupons.docs) == 3
    remaining_codes = [d["code"] for d in db.coupons.docs]
    assert "RAFT-SEP15" not in remaining_codes

    # Delete with status filter 'all': 2026-09-01 to 2026-09-30 -> Should delete remaining c1, c3
    success, count, msg = delete_coupons_by_date_range(db, "2026-09-01", "2026-09-30", status_filter="all")
    print("Delete All Status Result:", success, count, msg)
    assert success is True
    assert count == 2, f"Expected 2 deleted coupons, got {count}"
    assert len(db.coupons.docs) == 1
    assert db.coupons.docs[0]["code"] == "RAFT-OCT05"

    print("STATUS FILTRATION & DATE RANGE DELETION TEST PASSED!")

def test_single_coupon_deletion():
    db = MockDB()
    db.coupons.docs = [
        {"_id": "c10", "code": "RAFT-SINGLE1"},
        {"_id": "c11", "code": "RAFT-SINGLE2"},
    ]

    success, msg = delete_single_coupon(db, "c10")
    assert success is True
    assert len(db.coupons.docs) == 1
    assert db.coupons.docs[0]["code"] == "RAFT-SINGLE2"

    print("SINGLE COUPON DELETION TEST PASSED!")

if __name__ == "__main__":
    test_date_range_and_status_deletion()
    test_single_coupon_deletion()
