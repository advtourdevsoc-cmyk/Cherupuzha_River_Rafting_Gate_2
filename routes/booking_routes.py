from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from datetime import date, datetime, timedelta
from models.booking_model import create_booking
from utils.allocation_logic import load_settings, get_slots_for_date
from utils.amount_calculator import calculate_total_amount
from models.raft_model import ensure_rafts_for_date_slot
from bson.objectid import ObjectId
from datetime import timedelta as _timedelta
from utils.booking_ops import check_capacity_available
from utils.effective_availability import (
    calculate_effective_available_capacity,
    calculate_effective_available_capacity_from_rafts
)

booking_bp = Blueprint('booking', __name__)

def get_settings(db):
    """Get settings, using cache if available, otherwise load from DB."""
    # Always try to get from cache first for performance
    settings = current_app.config.get('SETTINGS_CACHE')
    if settings:
        return settings
    # If cache is empty, load from DB and cache it
    settings = load_settings(db)
    current_app.config['SETTINGS_CACHE'] = settings
    return settings

@booking_bp.route('/')
def home():
    settings = get_settings(current_app.mongo.db)
    return render_template('home.html', settings=settings)

@booking_bp.route('/check_coupon', methods=['GET'])
def check_coupon():
    db = current_app.mongo.db
    phone = request.args.get('phone', '')
    email = request.args.get('email', '')
    group_size = request.args.get('group_size', '')
    date = request.args.get('date', '')
    from models.coupon_model import get_active_coupon_by_phone
    coupon = get_active_coupon_by_phone(db, phone, email=email, group_size=group_size, booking_date=date)
    if coupon:
        return jsonify({
            'has_coupon': True,
            'code': coupon.get('code'),
            'discount_type': coupon.get('discount_type'),
            'discount_value': coupon.get('discount_value')
        })
    return jsonify({'has_coupon': False})

@booking_bp.route('/validate_coupon', methods=['POST'])
def validate_coupon_route():
    db = current_app.mongo.db
    data = request.get_json(silent=True) or request.form
    phone = data.get('phone', '')
    code = data.get('code', '')
    email = data.get('email', '')
    group_size = data.get('group_size')
    date = data.get('date', '') or data.get('booking_date', '')
    from models.coupon_model import validate_coupon
    coupon, err = validate_coupon(db, phone, code, email=email, group_size=group_size, booking_date=date)
    if err:
        return jsonify({'valid': False, 'message': err}), 400
    return jsonify({
        'valid': True,
        'code': coupon['code'],
        'discount_type': coupon.get('discount_type'),
        'discount_value': coupon.get('discount_value')
    })


@booking_bp.route('/book', methods=['GET','POST'])
def book():
    db = current_app.mongo.db
    settings = get_settings(db)
    
    # Determine allowed booking window based on admin settings (start_date and end_date)
    today = date.today()
    
    # Get start_date and end_date from settings
    start_date_str = settings.get('start_date')
    end_date_str = settings.get('end_date')
    
    # If dates are not set, fall back to old behavior (backward compatibility)
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            # Fallback to old calculation if date parsing fails
            number_of_booking_days = settings.get('days', 30)
            start_date = today
            end_date = today + timedelta(days=number_of_booking_days)
    else:
        # Backward compatibility: calculate from days
        number_of_booking_days = settings.get('days', 30)
        start_date = today
        end_date = today + timedelta(days=number_of_booking_days)
    
    # Ensure min_date is not before today (users can't book in the past)
    min_date = max(start_date, today)
    max_date = end_date

    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        location = request.form.get('location', '').strip()
        
        # Validate email ends with @gmail.com
        if not email or not email.strip().lower().endswith('@gmail.com'):
            
            return redirect(url_for('booking.book'))

        booking_date_str = request.form.get('booking_date')
        slot = request.form.get('slot')
        # validate booking date exists and is a proper future date
        if not booking_date_str:
            flash('Please provide a booking date.', 'error')
            return redirect(url_for('booking.book'))

        valid_time_slots = get_slots_for_date(db, booking_date_str, settings)
        if not slot or (valid_time_slots and slot not in valid_time_slots) or slot.strip().lower().startswith('select'):
            flash('Please select a valid time slot.', 'error')
            return redirect(url_for('booking.book'))
        try:
            booking_date = datetime.strptime(booking_date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Invalid booking date format.', 'error')
            return redirect(url_for('booking.book'))
        # Validate booking date is within allowed window [min_date, max_date]
        if booking_date < min_date or booking_date > max_date:
            flash(f'Booking date must be between {min_date.isoformat()} and {max_date.isoformat()} (inclusive).', 'error')
            return redirect(url_for('booking.book'))

        # NEW: Logic to prevent booking past slots for TODAY
        # If booking_date is today, we must ensure the slot hasn't passed.
        if booking_date == today:
            now = datetime.now()
            # Simple parser for slot start time (e.g. "7:00am" -> 7, "3:30pm" -> 15.5)
            # This logic should match client-side parsing or be robust.
            def parse_slot_start_hour_minutes(s):
                # extracts the time portion (e.g., "7:00am" or "3:30pm")
                # split by first non-digit char if not a colon? 
                # Assuming format like "7:00am-9:00am" or "1:00pm-3:00pm"
                try:
                    # Clean string to get the start part
                    start_str = s.split('–')[0].split('-')[0].split('to')[0].strip()
                    # Parse "7:00am" or "3:30pm"
                    if ':' in start_str:
                        hour, minute = map(int, start_str.split(':')[:2])
                    else:
                        hour = int(start_str)
                        minute = 0
                    
                    # Handle PM logic if manual pm text exists, but typically our settings use 24h or clear format.
                    # If the settings use 12h format (e.g. "3:30 PM"), we need to handle that.
                    is_pm = 'pm' in start_str.lower()
                    if is_pm and hour != 12:
                        hour += 12
                    
                    return hour, minute
                except:
                    return 0, 0 # Fallback, allow if parse fails (risky but better than blocking valid)

            slot_h, slot_m = parse_slot_start_hour_minutes(slot)
            
            # If current time > slot start time, block it. 
            # (Optionally add buffer, e.g. forbid booking 30 mins before)
            if now.hour > slot_h or (now.hour == slot_h and now.minute > slot_m):
                flash("We're sorry, today's booking window is closed. For any help or urgent bookings, please contact the rafting team at 8877994476.", 'error')
                return redirect(url_for('booking.book'))


        try:
            group_size = int(request.form.get('group_size'))
        except:
            flash('Invalid group size', 'error')
            return redirect(url_for('booking.book'))

        # Per-slot limits:
        # - normal_max_people_per_slot: standard 6-person rafts
        # Validate customer-facing group size against isolated effective available capacity
        eff_max = calculate_effective_available_capacity(db, booking_date_str, slot)
        if group_size < 1 or group_size > eff_max:
            if eff_max <= 0:
                flash('Selected slot is fully booked. Please select another slot or date.', 'error')
            else:
                flash(f'Invalid group size. Maximum available for this slot is {eff_max} people.', 'error')
            return redirect(url_for('booking.book'))

        # Server-side validation: reject if entire date is fully booked
        def is_date_fully_booked(db, date_str, settings):
            slots = get_slots_for_date(db, date_str, settings)
            for s in slots:
                if calculate_effective_available_capacity(db, date_str, s) > 0:
                    return False
            return True

        if is_date_fully_booked(db, booking_date_str, settings):
            flash('Selected date is fully booked', 'error')
            return redirect(url_for('booking.book'))

        # Capacity check (read‑only): ensure there is room for this group
        # without actually reserving seats or assigning rafts yet.
        # This keeps the flow:
        #   Pending booking -> no raft, no seat reserved.
        # If capacity is exhausted we still create a booking record, but
        # mark allow_payment=False so that the user cannot pay and the
        # admin can see the request as a capacity-exceeded case.
        has_capacity = check_capacity_available(db, booking_date_str, slot, group_size, allow_empty_for_small_group=False)
        if group_size < 4 and not has_capacity:
            flash('Oops! Your entered group size is below the minimum requirement.', 'error')
            return redirect(url_for('booking.book'))
        
        # Calculate amount for this booking
        amount_calc = calculate_total_amount(settings, booking_date_str, group_size)
        amount_per_person = amount_calc['applicable_amount']
        setting_price_amount = float(amount_per_person * group_size)
        advance_percent = amount_calc.get('advance_percent', 0)
        platform_fee_percent = amount_calc.get('platform_fee_percent', 0)

        coupon_code = request.form.get('coupon_code', '').strip().upper()
        discount_applied = 0
        applied_coupon_code = None

        if coupon_code:
            from models.coupon_model import validate_coupon
            coupon_doc, err = validate_coupon(db, phone, coupon_code, email=email, group_size=group_size, booking_date=booking_date_str)
            if coupon_doc and not err:
                applied_coupon_code = coupon_doc['code']
                dtype = coupon_doc.get('discount_type', 'per_head')
                dval = float(coupon_doc.get('discount_value', 0))
                if dtype == 'per_head':
                    per_head_discount = min(dval, amount_per_person)
                    discount_applied = round(per_head_discount * group_size, 2)
                elif dtype == 'flat':
                    discount_applied = min(dval, setting_price_amount)
                elif dtype == 'percentage':
                    discount_applied = round((setting_price_amount * dval) / 100.0, 2)
            else:
                flash(err or 'Invalid coupon code provided.', 'warning')

        # Recalculate final booking figures based on discounted subtotal
        final_calc = calculate_total_amount(
            settings,
            booking_date_str,
            group_size,
            amount_per_person=amount_per_person,
            coupon_discount=discount_applied
        )

        final_total_amount = final_calc['total_amount']
        final_advance_amount = final_calc['advance_amount']
        balance_amount = final_calc['balance_amount']
        platform_fee = final_calc['platform_fee']
        price_amount = final_calc['price_amount']

        original_total_amount = amount_calc['total_amount']
        original_advance_amount = amount_calc['advance_amount']

        booking_details = {
            'name': name,
            'email': email,
            'phone': phone,
            'location': location,
            'date': booking_date_str,
            'slot': slot,
            'group_size': group_size,
            'amount_per_person': amount_per_person,
            'price_amount': price_amount,
            'platform_fee': platform_fee,
            'total_amount': final_total_amount,
            'original_total_amount': original_total_amount,
            'advance_amount': final_advance_amount,
            'original_advance_amount': original_advance_amount,
            'balance_amount': balance_amount,
            'advance_percent': advance_percent,
            'platform_fee_percent': platform_fee_percent,
            'coupon_code': applied_coupon_code,
            'coupon_discount_applied': discount_applied
        }
        booking_id = create_booking(
            db,
            user_id=email,  # or current_user.get_id() if using Flask-Login
            booking_details=booking_details,
            amount=final_total_amount,
            currency='INR',
            status='Visited',
            payment_status='Pending',
            allow_payment=bool(has_capacity),
        )

        if has_capacity:
            flash('Booking created. Please complete payment to confirm your slot.', 'info')
        return redirect(url_for('booking.booking_confirmation', booking_id=booking_id))
    # Provide the min_date and max_date so the frontend datepicker can enforce range
    return render_template('booking.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), start_date=start_date.isoformat(), end_date=end_date.isoformat())

@booking_bp.route('/booking/<booking_id>/confirmation')
def booking_confirmation(booking_id):
    db = current_app.mongo.db
    try:
        b = db.bookings.find_one({'_id': ObjectId(booking_id)})
    except:
        b = None
    if not b:
        flash('Booking not found', 'error')
        return redirect(url_for('booking.home'))
        
    # If payment is already complete, check booking status (Confirmed -> success, Pending/Failed -> failure)
    status_str = str(b.get('status', '')).strip().lower()
    payment_status_str = str(b.get('payment_status', '')).strip().lower()
    if status_str == 'confirmed':
        return redirect(url_for('payment.payment_success', booking_id=str(b['_id'])))
    elif payment_status_str == 'paid' or status_str in ['failed', 'cancelled']:
        return redirect(url_for('payment.payment_failure', booking_id=str(b['_id'])))

    # Use stored advance amount and percent for fast response
    b['advance_amount'] = b.get('advance_amount', 0)
    b['advance_percent'] = b.get('advance_percent', 0)
    # Load platform_fee from booking, or recalculate from settings if missing
    if 'platform_fee' not in b or b.get('platform_fee') is None:
        from utils.allocation_logic import load_settings
        settings = load_settings(db)
        pf_pct = float(settings.get('platform_fee', 0) or 0)
        advance_val = float(b.get('advance_amount', 0))
        b['platform_fee'] = round(advance_val * (pf_pct / 100.0), 2)
    else:
        b['platform_fee'] = b.get('platform_fee', 0)
        
    from models.booking_model import get_remaining_seats
    date_str = b.get('date') or b.get('booking_details', {}).get('date', '')
    slot = b.get('slot') or b.get('booking_details', {}).get('slot', '')
    
    if date_str and slot:
        b['remaining_seats'] = get_remaining_seats(db, date_str, slot)
    else:
        b['remaining_seats'] = 0
        
    # minimum group size required when slot is empty
    b['min_group_size'] = 4
        
    b['_id'] = str(b['_id'])
    return render_template('booking_confirmation.html', booking=b)

@booking_bp.route('/availability')
def availability():
    """Get availability data - uses fresh settings to ensure accuracy."""
    db = current_app.mongo.db
    settings = get_settings(db)  # Uses cache if available, otherwise loads from DB
    today_str = date.today().isoformat()
    slots = get_slots_for_date(db, today_str, settings)
    total_capacity = settings['rafts_per_slot'] * settings['capacity']
    data = {}
    for slot in slots:
        available = calculate_effective_available_capacity(db, today_str, slot)
        percent_full = round(((total_capacity - available) / total_capacity) * 100, 2) if total_capacity > 0 else 0
        data[slot] = {'available': available, 'percent_full': max(0.0, percent_full)}
    return jsonify(data)


@booking_bp.route('/slot_availability')
def slot_availability():
    """Return availability info for a specific day. Query param: day=YYYY-MM-DD
    Response: { slot1: { available: N, full: bool }, slot2: {...} }
    """
    from flask import g
    import time
    
    day = request.args.get('day')
    if not day:
        return jsonify({}), 400

    db = current_app.mongo.db
    settings = get_settings(db)
    slots = get_slots_for_date(db, day, settings)
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)

    # Check if day is today to filter passed slots
    is_today = (day == date.today().isoformat())
    now = datetime.now()

    # Cache key to avoid repeated queries for same day
    cache_key = f"slot_avail_{day}"
    cached = getattr(g, '_slot_avail_cache', {}).get(cache_key)
    if cached and cached.get('expires_at', 0) > time.time():
        res = cached.get('data', {})
        # Filter out passed slots if today
        if is_today:
            import copy
            res = _filter_passed_slots(copy.deepcopy(res), slots, now)
        return jsonify(res)

    try:
        from models.raft_model import ensure_rafts_for_dates_and_slots
        ensure_rafts_for_dates_and_slots(db, [day], slots, rafts_per_slot, capacity)
        rafts_cursor = list(db.rafts.find({
            'day': day,
            'slot': {'$in': slots},
            'raft_id': {'$lte': rafts_per_slot}
        }).sort('raft_id', 1))
    except Exception:
        rafts_cursor = []

    slot_rafts = {s: [] for s in slots}
    for r in rafts_cursor:
        s = r.get('slot')
        if s in slot_rafts:
            slot_rafts[s].append(r)

    # Build slot availability map using effective availability logic
    res = {}
    for s in slots:
        rafts = slot_rafts.get(s, [])
        if not rafts:
            available = rafts_per_slot * (capacity + 1)
            max_small_group = 0
            has_small_group = False
        else:
            available = calculate_effective_available_capacity_from_rafts(rafts, rafts_per_slot, capacity)
            partial_vacs = [
                (7 if r.get('is_special') else capacity) - r.get('occupancy', 0)
                for r in rafts
                if not r.get('is_special', False) and 0 < r.get('occupancy', 0) < capacity
            ]
            max_small_group = max(partial_vacs, default=0)
            has_small_group = max_small_group > 0

        res[s] = {
            'available': available,
            'full': available <= 0,
            'has_small_group_capacity': has_small_group,
            'max_small_group': max_small_group
        }

    # Cache for 2 minutes (shorter for slot availability since it changes more frequently)
    if not hasattr(g, '_slot_avail_cache'):
        g._slot_avail_cache = {}
    g._slot_avail_cache[cache_key] = {
        'data': res,
        'expires_at': time.time() + 120  # 2 minutes
    }

    # Filter out passed slots if today
    if is_today:
        import copy
        res = _filter_passed_slots(copy.deepcopy(res), slots, now)

    return jsonify(res)


def _filter_passed_slots(res, slots, now):
    """Filter out slots that have already passed today."""
    for s in slots:
        try:
            start_str = s.split('–')[0].split('-')[0].split('to')[0].strip()
            if ':' in start_str:
                hh, mm = map(int, start_str.split(':')[:2])
            else:
                hh = int(start_str)
                mm = 0
            if 'pm' in start_str.lower() and hh != 12:
                hh += 12
            
            if now.hour > hh or (now.hour == hh and now.minute > mm):
                res[s] = {
                    'available': 0,
                    'full': True,
                    'has_small_group_capacity': False,
                    'max_small_group': 0
                }  # Mark as passed
        except Exception:
            pass
    return res


@booking_bp.route('/fully_booked_dates')
def fully_booked_dates():
    """Return a list of dates (ISO YYYY-MM-DD) within the booking window that are fully booked (all slots at 100%).
    
    OPTIMIZED for 1 CPU + 2GB RAM: Uses single aggregation query instead of N*M queries.
    """
    from flask import g
    import time
    
    db = current_app.mongo.db
    settings = get_settings(db)
    slots = settings.get('time_slots', [])
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)

    # Determine date window (same logic as book view)
    today = date.today()
    start_date_str = settings.get('start_date')
    end_date_str = settings.get('end_date')
    if start_date_str and end_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            start_date = today
            end_date = today + _timedelta(days=settings.get('days', 30))
    else:
        start_date = today
        end_date = today + _timedelta(days=settings.get('days', 30))

    # Cache key for this date range
    cache_key = f"fully_booked_{start_date.isoformat()}_{end_date.isoformat()}"
    cached = getattr(g, '_booked_dates_cache', {}).get(cache_key)
    if cached and cached.get('expires_at', 0) > time.time():
        return jsonify({'fully_booked_dates': cached.get('dates', [])})

    # Optimization: Fetch all relevant rafts for date range and compute effective availability
    try:
        rafts_cursor = list(db.rafts.find({
            'day': {'$gte': start_date.isoformat(), '$lte': end_date.isoformat()},
            'slot': {'$in': slots},
            'raft_id': {'$lte': rafts_per_slot}
        }).sort('raft_id', 1))
    except Exception:
        return jsonify({'fully_booked_dates': []})

    day_slot_rafts = {}
    for r in rafts_cursor:
        d = r.get('day')
        s = r.get('slot')
        if d not in day_slot_rafts:
            day_slot_rafts[d] = {slot_name: [] for slot_name in slots}
        if s in day_slot_rafts[d]:
            day_slot_rafts[d][s].append(r)

    # Build a map: day -> {slot -> available}
    day_slot_vacancy = {}
    for d, slot_dict in day_slot_rafts.items():
        day_slot_vacancy[d] = {}
        for s, rafts in slot_dict.items():
            if not rafts:
                avail = rafts_per_slot * (capacity + 1)
            else:
                avail = calculate_effective_available_capacity_from_rafts(rafts, rafts_per_slot, capacity)
            day_slot_vacancy[d][s] = avail

    # Determine fully booked dates: all slots have 0 vacancy
    fully_booked = []
    cur = start_date
    while cur <= end_date:
        ds = cur.isoformat()
        slot_vacancies = day_slot_vacancy.get(ds, {})
        
        # If we have data for this day, check if all slots are full
        if slot_vacancies:
            all_slots_full = all(slot_vacancies.get(s, 0) <= 0 for s in slots)
            if all_slots_full:
                fully_booked.append(ds)
        # If no data, assume day is not fully booked (rafts will be created on demand)
        
        cur = cur + _timedelta(days=1)

    # Cache for 5 minutes
    if not hasattr(g, '_booked_dates_cache'):
        g._booked_dates_cache = {}
    g._booked_dates_cache[cache_key] = {
        'dates': fully_booked,
        'expires_at': time.time() + 300  # 5 minutes
    }

    return jsonify({'fully_booked_dates': fully_booked})

@booking_bp.route('/track-booking', methods=['GET','POST'])
def track_booking():
    if request.method == 'POST':
        email = request.form.get('email')
        phone = request.form.get('phone')
        
        # Validate inputs
        if not email or not phone:
            flash('Please provide both email and phone number.', 'error')
            return redirect(url_for('booking.track_booking'))
        
        db = current_app.mongo.db
        # Required to match BOTH email and phone for security/accuracy
        cursor = db.bookings.find({
            'email': email, 'phone': phone
        }).sort('created_at', -1)
        # Note: find_latest_by_contact currently sorts and checks. 
        # Ideally, we should use a new function `find_all_by_contact` or reuse the cursor if it wasn't limited to 1.
        # Assuming `find_latest_by_contact` returns a mongo cursor (not find_one), we can iterate it. 
        # Let's inspect `models/booking_model.py` carefully or just write the find query here to be safe.
        
        bookings = list(cursor)
        
        if not bookings:
            flash('No booking found for that contact.', 'error')
            return redirect(url_for('booking.track_booking'))
        
        # Filter out past bookings - only show upcoming bookings (today and future)
        today = date.today().isoformat()
        upcoming_bookings = [b for b in bookings if b.get('date') and b.get('date') >= today]
        
        if not upcoming_bookings:
            flash('No upcoming bookings found. Your bookings are in the past.', 'info')
            return redirect(url_for('booking.track_booking'))
        
        # Convert ObjectId to string for template rendering
        for b in upcoming_bookings:
            b['_id'] = str(b.get('_id'))
            
            # Ensure advance_amount and advance_percent are present for display
            if not b.get('advance_amount'):
                settings = get_settings(db)
                amount_calc = calculate_total_amount(settings, b.get('date'), int(b.get('group_size', 0) or 0))
                b['advance_amount'] = amount_calc.get('advance_amount', 0)
                b['advance_percent'] = amount_calc.get('advance_percent', 0)
            
        return render_template('track_booking_result.html', bookings=upcoming_bookings)
    return render_template('track_booking.html')
