import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import traceback
from datetime import datetime, date, timezone
from bson import ObjectId

# Import utils modules
import utils.allocation_logic as alloc
import utils.amount_calculator as calc
import utils.booking_ops as ops
import utils.effective_availability as eff
import utils.settings_manager as set_mgr
import models.booking_model as b_model
import models.coupon_model as c_model
import models.raft_model as r_model
import models.user_model as u_model

from scratch.test_all_routes_and_templates import MockDatabase

def test_utils_and_models():
    db = MockDatabase()

    print("--- 1. Testing allocation_logic.py ---")
    try:
        s = alloc.load_settings(db)
        print("load_settings:", type(s))
    except Exception as e:
        print("[FAIL] load_settings:", e)

    try:
        res = alloc.allocate_raft(db, None, "2026-10-08", "7:00am", 4)
        print("allocate_raft status:", res.get('status'))
    except Exception as e:
        print("[FAIL] allocate_raft:", e)
        traceback.print_exc()

    print("\n--- 2. Testing amount_calculator.py ---")
    try:
        settings = alloc.load_settings(db)
        res = calc.calculate_total_amount(settings, "2026-10-08", 4)
        print("calculate_total_amount total:", res.get('total_amount'))
    except Exception as e:
        print("[FAIL] calculate_total_amount:", e)
        traceback.print_exc()

    print("\n--- 3. Testing booking_ops.py ---")
    # Insert a dummy booking
    b_id = db.bookings.insert_one({
        '_id': ObjectId(),
        'date': '2026-10-08',
        'slot': '7:00am',
        'group_size': 4,
        'status': 'Confirmed',
        'raft_allocations': [1]
    }).inserted_id

    try:
        c_res = ops.cancel_booking(db, b_id)
        print("cancel_booking:", c_res)
    except Exception as e:
        print("[FAIL] cancel_booking:", e)
        traceback.print_exc()

    try:
        p_res = ops.postpone_booking(db, b_id, "2026-10-09", "10:00am")
        print("postpone_booking:", p_res)
    except Exception as e:
        print("[FAIL] postpone_booking:", e)
        traceback.print_exc()

    try:
        r_res = ops.reduce_booking_group_size(db, b_id, 2)
        print("reduce_booking_group_size:", r_res)
    except Exception as e:
        print("[FAIL] reduce_booking_group_size:", e)
        traceback.print_exc()

    try:
        cap_res = ops.check_capacity_available(db, "2026-10-08", "7:00am", 4)
        print("check_capacity_available:", cap_res)
    except Exception as e:
        print("[FAIL] check_capacity_available:", e)
        traceback.print_exc()

    print("\n--- 4. Testing effective_availability.py ---")
    try:
        eff_cap = eff.calculate_effective_available_capacity(db, "2026-10-08", "7:00am")
        print("calculate_effective_available_capacity:", eff_cap)
    except Exception as e:
        print("[FAIL] calculate_effective_available_capacity:", e)
        traceback.print_exc()

    print("\n--- 5. Testing coupon_model.py ---")
    try:
        c_doc = c_model.create_coupon(db, "9876543210", "per_head", 100, "admin", "test@gmail.com", 5)
        print("create_coupon code:", c_doc['code'])
        v_doc, v_err = c_model.validate_coupon(db, "9876543210", c_doc['code'], email="test@gmail.com", group_size=5)
        print("validate_coupon:", v_doc is not None, v_err)
    except Exception as e:
        print("[FAIL] coupon_model:", e)
        traceback.print_exc()

if __name__ == '__main__':
    test_utils_and_models()
