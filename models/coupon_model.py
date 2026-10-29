import random
import string
from datetime import datetime, timezone, timedelta
from bson.objectid import ObjectId

def normalize_phone(phone):
    """Normalize phone number to standard 10-digit format."""
    if not phone:
        return ""
    digits = ''.join(c for c in str(phone) if c.isdigit())
    if len(digits) > 10 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) > 10 and digits.startswith('0'):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits

def generate_coupon_code():
    """Generate code in format RAFT-XXXX (e.g. RAFT-K9X2)."""
    chars = string.ascii_uppercase + string.digits
    # Exclude ambiguous characters O, 0, I, 1 for clarity
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    suffix = ''.join(random.choices(chars, k=4))
    return f"RAFT-{suffix}"

def is_coupon_expired(coupon):
    """
    Check if a coupon has passed its 12-hour validity period from creation.
    """
    if not coupon:
        return True

    now = datetime.now(timezone.utc)
    expires_at = coupon.get("expires_at")
    created_at = coupon.get("created_at")

    if not expires_at and created_at:
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            expires_at = created_at + timedelta(hours=12)

    if not expires_at:
        return False

    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return now >= expires_at

    return False

def is_day_type_valid_for_coupon(coupon, booking_date):
    """
    Check if a booking date matches the coupon's valid_day_types ('weekday' for Mon-Fri, 'weekend' for Sat-Sun).
    """
    if not booking_date:
        return True
    valid_day_types = coupon.get("valid_day_types")
    if not valid_day_types:
        dt = coupon.get("day_type")
        if dt:
            valid_day_types = [str(dt).lower()]
        else:
            valid_day_types = ["weekday", "weekend"]
    else:
        valid_day_types = [str(x).lower() for x in valid_day_types]

    if "weekday" in valid_day_types and "weekend" in valid_day_types:
        return True

    if isinstance(booking_date, str):
        try:
            dt_obj = datetime.strptime(booking_date.strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return True
    elif isinstance(booking_date, date):
        dt_obj = booking_date
    else:
        return True

    # Monday is 0, Sunday is 6
    w = dt_obj.weekday()
    actual_type = "weekend" if w in (5, 6) else "weekday"
    return actual_type in valid_day_types


def create_coupon(db, phone, discount_type, discount_value, created_by="admin", email=None, group_size=None, valid_day_types=None):
    """
    Create a new one-time coupon tied to a customer phone number, email, group size, and valid day types (weekday/weekend).
    """
    clean_phone = normalize_phone(phone)
    if not clean_phone or len(clean_phone) != 10:
        raise ValueError("Valid 10-digit phone number is required.")

    if not email or not str(email).strip():
        raise ValueError("Customer email is required.")
    clean_email = str(email).strip().lower()
    if not clean_email.endswith("@gmail.com"):
        raise ValueError("Please enter a valid Gmail address (ending in @gmail.com).")

    if group_size is None or str(group_size).strip() == "":
        raise ValueError("Group size is required.")

    try:
        parsed_group_size = int(group_size)
        if parsed_group_size <= 0:
            raise ValueError("Group size must be greater than zero.")
    except (ValueError, TypeError):
        raise ValueError("Valid group size is required.")

    # Process valid_day_types
    if valid_day_types is None:
        clean_day_types = ["weekday", "weekend"]
    elif isinstance(valid_day_types, str):
        clean_day_types = [valid_day_types.strip().lower()]
    else:
        clean_day_types = [str(x).strip().lower() for x in valid_day_types if str(x).strip().lower() in ["weekday", "weekend"]]
    if not clean_day_types:
        clean_day_types = ["weekday", "weekend"]

    # Check if an active/valid (non-expired) coupon already exists for this customer (phone or email)
    existing_coupons = list(db.coupons.find({
        "$or": [
            {"phone": clean_phone},
            {"email": clean_email}
        ],
        "is_used": False,
        "is_revoked": {"$ne": True},
        "is_deleted": {"$ne": True}
    }))
    for ec in existing_coupons:
        if not is_coupon_expired(ec):
            raise ValueError("A valid coupon already exists for this customer.")

    if discount_type not in ["per_head", "flat", "percentage"]:
        raise ValueError("Invalid discount type.")

    val = float(discount_value)
    if val <= 0:
        raise ValueError("Discount value must be greater than zero.")
    if discount_type == "percentage" and val > 100:
        raise ValueError("Percentage discount cannot exceed 100%.")

    # Generate unique code
    attempts = 0
    while attempts < 10:
        code = generate_coupon_code()
        if not db.coupons.find_one({"code": code}):
            break
        attempts += 1

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(hours=12)

    coupon_doc = {
        "code": code,
        "phone": clean_phone,
        "email": email.strip().lower() if email else None,
        "group_size": parsed_group_size,
        "valid_day_types": clean_day_types,
        "discount_type": discount_type,
        "discount_value": val,
        "is_used": False,
        "is_revoked": False,
        "is_deleted": False,
        "created_at": created_at,
        "expires_at": expires_at,
        "created_by": created_by,
        "used_at": None,
        "booking_id": None
    }

    result = db.coupons.insert_one(coupon_doc)
    coupon_doc["_id"] = result.inserted_id
    return coupon_doc


def get_active_coupon_by_phone(db, phone, email=None, group_size=None, booking_date=None):
    """
    Find active (unused, not revoked, and non-expired) coupon matching phone number, email, group size, and booking date.
    """
    clean_phone = normalize_phone(phone)
    if not clean_phone or len(clean_phone) != 10:
        return None
    candidates = list(db.coupons.find(
        {"phone": clean_phone, "is_used": False, "is_revoked": {"$ne": True}, "is_deleted": {"$ne": True}},
        sort=[("created_at", -1)]
    ))
    for c in candidates:
        if is_coupon_expired(c):
            continue

        # 1. Email ID Check
        stored_email = str(c.get("email")).strip().lower() if c.get("email") else ""
        if stored_email:
            clean_email = str(email).strip().lower() if email else ""
            if not clean_email or clean_email != stored_email:
                continue

        # 2. Group Size Check: Applicable when requested group size >= generated group size
        stored_group_size = c.get("group_size")
        if stored_group_size is not None:
            try:
                req_group_size = int(group_size) if group_size is not None and str(group_size).strip() != "" else None
            except (ValueError, TypeError):
                req_group_size = None

            if req_group_size is None or req_group_size < int(stored_group_size):
                continue

        # 3. Day Type Check (Weekday vs Weekend)
        if not is_day_type_valid_for_coupon(c, booking_date):
            continue

        return c
    return None

def validate_coupon(db, phone, code, email=None, group_size=None, booking_date=None):
    """
    Validate a coupon code against phone number, email ID, group size, and booking date, enforcing 12-hour expiry.
    Applicable when requested group size >= generated group size.
    """
    clean_phone = normalize_phone(phone)
    if not clean_phone or not code:
        return None, "Phone number and coupon code are required."

    clean_code = str(code).strip().upper()
    coupon = db.coupons.find_one({"code": clean_code})

    if not coupon:
        return None, "Invalid coupon code."

    if coupon.get("is_revoked") or coupon.get("is_deleted"):
        return None, "This coupon has been revoked."

    if coupon.get("is_used"):
        return None, "This coupon has already been used."

    if is_coupon_expired(coupon):
        return None, "This coupon code has expired."

    # 1. Phone Number Check
    if normalize_phone(coupon.get("phone")) != clean_phone:
        return None, "Coupon details do not match the current booking."

    # 2. Email ID Check
    stored_email = str(coupon.get("email")).strip().lower() if coupon.get("email") else ""
    if stored_email:
        clean_email = str(email).strip().lower() if email else ""
        if not clean_email or clean_email != stored_email:
            return None, "Coupon details do not match the current booking."

    # 3. Group Size Check: Applicable when requested group size >= generated group size
    stored_group_size = coupon.get("group_size")
    if stored_group_size is not None:
        try:
            req_group_size = int(group_size) if group_size is not None and str(group_size).strip() != "" else None
        except (ValueError, TypeError):
            req_group_size = None

        if req_group_size is None or req_group_size < int(stored_group_size):
            return None, f"Group size must be at least {stored_group_size} to use this coupon."

    # 4. Day Type Check (Weekday vs Weekend)
    if not is_day_type_valid_for_coupon(coupon, booking_date):
        valid_day_types = coupon.get("valid_day_types") or ["weekday", "weekend"]
        if "weekday" in valid_day_types and "weekend" not in valid_day_types:
            return None, "This coupon is only valid for weekday (Monday–Friday) bookings."
        elif "weekend" in valid_day_types and "weekday" not in valid_day_types:
            return None, "This coupon is only valid for weekend (Saturday–Sunday) bookings."
        return None, "This coupon is not valid for the selected booking date."

    return coupon, None

def mark_coupon_used(db, code, booking_id):
    """
    Mark coupon as used and link to booking_id.
    """
    if not code:
        return False
    clean_code = str(code).strip().upper()
    update_data = {
        "is_used": True,
        "used_at": datetime.now(timezone.utc)
    }
    if booking_id:
        update_data["booking_id"] = ObjectId(booking_id) if isinstance(booking_id, str) else booking_id

    result = db.coupons.update_one(
        {"code": clean_code, "is_used": False, "is_revoked": {"$ne": True}, "is_deleted": {"$ne": True}},
        {"$set": update_data}
    )
    return result.modified_count > 0

def revoke_coupon(db, coupon_id):
    """
    Revoke (soft-delete) a coupon by its ID or code.
    Used coupons cannot be revoked to preserve booking history.
    """
    if not coupon_id:
        return False, "Coupon ID or code is required."

    coupon = None
    try:
        obj_id = ObjectId(coupon_id) if isinstance(coupon_id, str) else coupon_id
        coupon = db.coupons.find_one({"_id": obj_id})
    except Exception:
        pass

    if not coupon:
        clean_code = str(coupon_id).strip().upper()
        coupon = db.coupons.find_one({"code": clean_code})

    if not coupon:
        return False, "Coupon not found."

    if coupon.get("is_revoked") or coupon.get("is_deleted"):
        return False, "Coupon is already revoked."

    if coupon.get("is_used"):
        return False, "Used coupons cannot be revoked."

    result = db.coupons.update_one(
        {"_id": coupon["_id"]},
        {"$set": {
            "is_revoked": True,
            "is_deleted": True,
            "revoked_at": datetime.now(timezone.utc)
        }}
    )
    if result.modified_count > 0:
        return True, f"Coupon {coupon.get('code')} revoked successfully."
    return False, "Failed to revoke coupon."


def get_all_coupons(db, query_filter=None):
    """
    Retrieve all coupons sorted by creation date (newest first).
    Optionally accepts a query_filter dict.
    """
    q = query_filter or {}
    return list(db.coupons.find(q).sort("created_at", -1))


def delete_coupons_by_date_range(db, start_date_str, end_date_str, status_filter='all'):
    """
    Delete coupons generated between start_date_str and end_date_str (in YYYY-MM-DD format)
    with optional status_filter ('all', 'Valid', 'Used', 'Expired', 'Revoked').
    Returns (success, deleted_count, message).
    """
    if not start_date_str or not end_date_str:
        return False, 0, "Both From Date and To Date are required."

    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d').replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
    except ValueError:
        return False, 0, "Invalid date format. Expected YYYY-MM-DD."

    if start_dt > end_dt:
        return False, 0, "From Date cannot be later than To Date."

    all_coupons = list(db.coupons.find())
    target_ids = []

    for c in all_coupons:
        # Check date range
        cat = c.get('created_at')
        if not cat:
            continue

        in_range = False
        if isinstance(cat, datetime):
            if cat.tzinfo is None:
                cat = cat.replace(tzinfo=timezone.utc)
            if start_dt <= cat <= end_dt:
                in_range = True
        elif isinstance(cat, str):
            try:
                cat_dt = datetime.fromisoformat(cat.replace('Z', '+00:00'))
                if cat_dt.tzinfo is None:
                    cat_dt = cat_dt.replace(tzinfo=timezone.utc)
                if start_dt <= cat_dt <= end_dt:
                    in_range = True
            except Exception:
                if start_date_str <= cat[:10] <= end_date_str:
                    in_range = True

        if not in_range:
            continue

        # Determine coupon status
        if c.get("is_revoked") or c.get("is_deleted"):
            c_status = "Revoked"
        elif c.get("is_used"):
            c_status = "Used"
        elif is_coupon_expired(c):
            c_status = "Expired"
        else:
            c_status = "Valid"

        # Check status filter
        if status_filter and str(status_filter).strip().lower() != "all":
            if c_status.lower() != str(status_filter).strip().lower():
                continue

        target_ids.append(c['_id'])

    count = 0
    if target_ids:
        result = db.coupons.delete_many({"_id": {"$in": target_ids}})
        count = result.deleted_count

    status_info = f" with status '{status_filter}'" if status_filter and str(status_filter).strip().lower() != "all" else ""
    if count > 0:
        return True, count, f"Successfully deleted {count} coupon(s){status_info} generated between {start_date_str} and {end_date_str}."
    return True, 0, f"No coupons found{status_info} generated between {start_date_str} and {end_date_str}."


def delete_single_coupon(db, coupon_id):
    """
    Delete a single coupon entry from database history.
    """
    if not coupon_id:
        return False, "Coupon ID is required."
    try:
        if isinstance(coupon_id, str) and ObjectId.is_valid(coupon_id):
            obj_id = ObjectId(coupon_id)
            res = db.coupons.delete_one({"_id": obj_id})
            if res.deleted_count > 0:
                return True, "Coupon deleted successfully."
    except Exception:
        pass

    # Try raw _id match
    res = db.coupons.delete_one({"_id": coupon_id})
    if res.deleted_count > 0:
        return True, "Coupon deleted successfully."

    clean_code = str(coupon_id).strip().upper()
    res = db.coupons.delete_one({"code": clean_code})
    if res.deleted_count > 0:
        return True, "Coupon deleted successfully."

    return False, "Coupon not found."


