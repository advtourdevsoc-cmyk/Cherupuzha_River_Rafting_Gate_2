import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import traceback
from datetime import datetime, date, timezone
from bson import ObjectId

from routes.admin_routes import utc_to_ist, _booking_sort_key
from utils.amount_calculator import calculate_total_amount
from models.coupon_model import (
    create_coupon,
    get_active_coupon_by_phone,
    validate_coupon,
    delete_coupons_by_date_range,
    delete_single_coupon
)

def test_utc_to_ist_edge_cases():
    print("Testing utc_to_ist edge cases...")
    # 1. None
    assert utc_to_ist(None) == ""
    # 2. Valid datetime with tz
    dt1 = datetime.now(timezone.utc)
    res1 = utc_to_ist(dt1)
    assert res1 != ""
    # 3. Naive datetime
    dt2 = datetime.now()
    res2 = utc_to_ist(dt2)
    assert res2 != ""
    # 4. String datetime (legacy or mongo string date)
    try:
        res3 = utc_to_ist("2026-10-08T10:00:00Z")
        print(f"utc_to_ist string handled: {res3}")
    except Exception as e:
        print(f"[FAIL] utc_to_ist threw exception on string: {type(e).__name__}: {e}")

def test_booking_sort_key_edge_cases():
    print("Testing _booking_sort_key edge cases...")
    time_slots = ['7:00am', '10:00am']
    # 1. Normal booking
    b1 = {'date': '2026-10-08', 'slot': '7:00am', 'created_at': datetime.now(timezone.utc)}
    k1 = _booking_sort_key(b1, time_slots)

    # 2. time_slots is None
    try:
        k2 = _booking_sort_key(b1, None)
        print("sort key with time_slots=None handled")
    except Exception as e:
        print(f"[FAIL] _booking_sort_key threw exception on time_slots=None: {type(e).__name__}: {e}")

    # 3. created_at is string
    b3 = {'date': '2026-10-08', 'slot': '7:00am', 'created_at': '2026-10-08T10:00:00'}
    try:
        k3 = _booking_sort_key(b3, time_slots)
        print("sort key with created_at as string handled")
    except Exception as e:
        print(f"[FAIL] _booking_sort_key threw exception on created_at string: {type(e).__name__}: {e}")

def test_amount_calculator_edge_cases():
    print("Testing calculate_total_amount edge cases...")
    settings = {
        'weekday_amount': 1150,
        'saturday_amount': 1350,
        'weekday_advance_percent': 25.0,
        'saturday_advance_percent': 25.0,
        'platform_fee': 2.4
    }

    # 1. group_size = 0
    res0 = calculate_total_amount(settings, "2026-10-08", 0)
    assert res0['total_amount'] == 0

    # 2. settings missing keys
    res_empty = calculate_total_amount({}, "2026-10-08", 5)
    print(f"calculate_total_amount with empty settings: {res_empty['total_amount']}")

    # 3. Invalid date_str format
    try:
        res_invalid_date = calculate_total_amount(settings, "invalid-date", 5)
        print(f"calculate_total_amount with invalid date: {res_invalid_date['total_amount']}")
    except Exception as e:
        print(f"[FAIL] calculate_total_amount threw exception on invalid date: {type(e).__name__}: {e}")

if __name__ == '__main__':
    test_utc_to_ist_edge_cases()
    test_booking_sort_key_edge_cases()
    test_amount_calculator_edge_cases()
