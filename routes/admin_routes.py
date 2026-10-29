import logging
from bson.objectid import ObjectId
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify, make_response, session
from flask_login import login_required, current_user
from utils.allocation_logic import load_settings, get_slots_for_date, get_all_active_slots, get_slots_for_date_range, _parse_time_slot
from utils.booking_ops import cancel_booking, postpone_booking
from utils.settings_manager import invalidate_settings_cache, refresh_settings_cache, regenerate_rafts_for_settings_change
from utils.email_service import send_postponement_email, send_cancellation_email, send_coupon_email, send_group_size_edit_email
from models.booking_model import create_booking, update_booking_status
from models.coupon_model import create_coupon, get_all_coupons, revoke_coupon
from utils.amount_calculator import calculate_total_amount

import datetime
import threading
import os

from datetime import timezone, timedelta

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

def utc_to_ist(dt, fmt="%d-%m-%Y %I:%M %p"):
    if not dt:
        return ""

    # ✅ FIX: handle strings, old/naive UTC datetimes safely
    if not hasattr(dt, 'tzinfo'):
        if isinstance(dt, str):
            try:
                dt = datetime.datetime.fromisoformat(dt.replace('Z', '+00:00'))
            except Exception:
                return str(dt)
        else:
            return str(dt)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(IST).strftime(fmt)


def _regenerate_rafts_async(mongo_uri, old_settings, new_settings):
    """
    Background task to regenerate rafts asynchronously.
    Takes mongo_uri instead of app context to avoid threading issues.
    """
    try:
        from pymongo import MongoClient
        print("[SETTINGS-ASYNC] Starting background raft regeneration...")
        
        # Create independent MongoDB connection for this thread
        client = MongoClient(mongo_uri)
        db = client.get_database()
        
        # Run regeneration in this thread
        regenerate_rafts_for_settings_change(db, old_settings, new_settings)
        print("[SETTINGS-ASYNC] Background raft regeneration complete!")
        
        client.close()
    except Exception as e:
        print(f"[SETTINGS-ASYNC] Error during raft regeneration: {str(e)}")


def _async_sync_payments(mongo_uri, max_check=20):
    """
    Background task to auto-sync captured Razorpay payments asynchronously.
    Prevents blocking main HTTP thread during dashboard loads.
    """
    try:
        from pymongo import MongoClient
        client = MongoClient(mongo_uri)
        db = client.get_database()
        sync_captured_razorpay_payments(db, max_check=max_check)
        client.close()
    except Exception as e:
        logger.warning(f"[PAYMENT-SYNC-ASYNC] Background auto-sync error: {str(e)}")


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('Admin only', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated

def supreme_admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_supreme_admin():
            flash('Supreme Admin access required', 'error')
            return redirect(url_for('admin.dashboard'))
        return f(*args, **kwargs)
    return decorated

def subadmin_or_admin_required(f):
    """Allow both admin and subadmin to access the route."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin_or_subadmin():
            flash('Access denied', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated

def _booking_sort_key(booking, time_slots):
    """Sort key for All Bookings: date ASC, then slot by configured time order, then created_at DESC."""
    date_val = booking.get('date') or ''
    slot = booking.get('slot') or ''
    slots_list = time_slots or []
    try:
        slot_index = slots_list.index(slot) if slot in slots_list else len(slots_list)
    except (ValueError, TypeError):
        slot_index = len(slots_list)
    created = booking.get('created_at')
    created_ts = created.timestamp() if hasattr(created, 'timestamp') and created else 0
    return (date_val, slot_index, -created_ts)


@admin_bp.route('/dashboard')
@login_required
@subadmin_or_admin_required
def dashboard():
    db = current_app.mongo.db
    settings = load_settings(db)
    time_slots = get_all_active_slots(db, settings)
    dashboard_settings = dict(settings)
    dashboard_settings['time_slots'] = time_slots
    # Build query filter for bookings list (filters apply only to bookings)
    # Default behaviour for Admin:
    #   - Show only Confirmed bookings (paid / capacity-reserved).
    #   - Pending bookings are primarily capacity-exceeded / manual review
    #     cases and are hidden by default.
    query_filter = {}

    # DB sort: date and created_at; slot order applied in Python using configured time_slots
    sort_order = [('date', 1), ('created_at', -1)]

    # If subadmin, do not apply user-supplied filters — show Confirmed bookings for today and tomorrow separately
    if current_user.is_subadmin():
        today = datetime.date.today().isoformat()
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        
        # Get slot filter from query params if provided
        slot_filter = request.args.get('slot', '').strip()

        # Build query filters for today and tomorrow
        today_filter = {'date': today, 'status': 'Confirmed'}
        tomorrow_filter = {'date': tomorrow, 'status': 'Confirmed'}
        
        # Apply slot filter if provided
        if slot_filter:
            today_filter['slot'] = slot_filter
            tomorrow_filter['slot'] = slot_filter

        # Fetch Today's Bookings and sort by date then slot order
        bookings_today = list(db.bookings.find(today_filter).sort(sort_order))
        bookings_today.sort(key=lambda b: _booking_sort_key(b, time_slots))
        for b in bookings_today:
            b['created_at_ist'] = utc_to_ist(b.get('created_at'))
            b['is_admin_booking'] = bool(b.get('is_admin_booking') or b.get('booking_details', {}).get('is_admin_booking'))
            # Use stored pricing to prevent settings changes from modifying existing bookings
            total = b.get('amount') or b.get('total_amount')
            advance = b.get('advance_amount')
            if advance is None or total is None:
                try:
                    gs_val = int(b.get('group_size', 0) or 0)
                except (ValueError, TypeError):
                    gs_val = 0
                amt = calculate_total_amount(settings, b.get('date'), gs_val)
                total = total if total is not None else amt.get('total_amount', 0)
                advance = advance if advance is not None else amt.get('advance_amount', 0)
                b['advance_percent'] = amt.get('advance_percent', 0)
            else:
                b['advance_percent'] = b.get('advance_percent', 0)
            
            total_val = float(total if total is not None else 0)
            advance_val = float(advance if advance is not None else 0)
            b['total_amount'] = total_val
            b['advance_amount'] = advance_val
            paid = str(b.get('payment_status', '')).lower() == 'paid'
            b['payment_status_display'] = 'Paid' if paid else 'Not paid'
            if b.get('balance_amount') is not None:
                b['balance_amount'] = float(b.get('balance_amount'))
            else:
                b['balance_amount'] = max(total_val - advance_val, 0.0) if paid else total_val

        # Fetch Tomorrow's Bookings and sort by date then slot order
        bookings_tomorrow = list(db.bookings.find(tomorrow_filter).sort(sort_order))
        bookings_tomorrow.sort(key=lambda b: _booking_sort_key(b, time_slots))
        for b in bookings_tomorrow:
            b['created_at_ist'] = utc_to_ist(b.get('created_at'))
            b['is_admin_booking'] = bool(b.get('is_admin_booking') or b.get('booking_details', {}).get('is_admin_booking'))
            total = b.get('amount') or b.get('total_amount')
            advance = b.get('advance_amount')
            if advance is None or total is None:
                try:
                    gs_val = int(b.get('group_size', 0) or 0)
                except (ValueError, TypeError):
                    gs_val = 0
                amt = calculate_total_amount(settings, b.get('date'), gs_val)
                total = total if total is not None else amt.get('total_amount', 0)
                advance = advance if advance is not None else amt.get('advance_amount', 0)
                b['advance_percent'] = amt.get('advance_percent', 0)
            else:
                b['advance_percent'] = b.get('advance_percent', 0)
            
            total_val = float(total if total is not None else 0)
            advance_val = float(advance if advance is not None else 0)
            b['total_amount'] = total_val
            b['advance_amount'] = advance_val
            paid = str(b.get('payment_status', '')).lower() == 'paid'
            b['payment_status_display'] = 'Paid' if paid else 'Not paid'
            if b.get('balance_amount') is not None:
                b['balance_amount'] = float(b.get('balance_amount'))
            else:
                b['balance_amount'] = max(total_val - advance_val, 0.0) if paid else total_val

        subadmin_slots = get_slots_for_date_range(db, today, tomorrow, settings)
        today_str = datetime.date.today().isoformat()
        response = make_response(render_template('admin_dashboard.html',
                             bookings_today=bookings_today,
                             bookings_tomorrow=bookings_tomorrow,
                             is_subadmin=True,
                             settings=dashboard_settings,
                             all_slots=subadmin_slots,
                             filter_from=today, # Default views for reference
                             filter_to=tomorrow,
                             filter_slot=slot_filter,
                             filter_status='',
                             today_str=today_str))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    # For admin: Read filter params from query string
    from_date = request.args.get('from', '').strip()
    to_date = request.args.get('to', '').strip()
    slot_filter = request.args.get('slot', '').strip()
    status_filter = request.args.get('status', '').strip()

    # Validate and build date range filter
    if from_date and to_date:
        try:
            f = datetime.date.fromisoformat(from_date)
            t = datetime.date.fromisoformat(to_date)
            if f > t:
                flash('From Date must not be later than To Date', 'error')
                # ignore date filters on invalid range
            else:
                query_filter['date'] = {'$gte': from_date, '$lte': to_date}
        except (ValueError, TypeError):
            flash('Invalid date format for filters', 'error')
    elif from_date:
        try:
            datetime.date.fromisoformat(from_date)
            query_filter['date'] = {'$gte': from_date}
        except (ValueError, TypeError):
            flash('Invalid From date', 'error')
    elif to_date:
        try:
            datetime.date.fromisoformat(to_date)
            query_filter['date'] = {'$lte': to_date}
        except (ValueError, TypeError):
            flash('Invalid To date', 'error')

    # If NO date filter is provided for Admin, default to TODAY
    if not from_date and not to_date:
        today_str = datetime.date.today().isoformat()
        query_filter['date'] = today_str
        # We set from/to variables so they appear in the UI inputs
        from_date = today_str
        to_date = today_str

    # Slot filter
    if slot_filter:
        query_filter['slot'] = slot_filter

    # Status filter
    if not status_filter:
        # Default: only confirmed bookings
        query_filter['status'] = 'Confirmed'
    elif status_filter.lower() == 'all':
        # "All" shows all statuses: Visited, Pending, Confirmed, Cancelled, Failed
        pass
    elif status_filter.lower() == 'visited':
        query_filter['status'] = {'$in': ['Visited', 'visited']}
    elif status_filter == 'Pending':
        query_filter['status'] = 'Pending'
    elif status_filter == 'Confirmed':
        query_filter['status'] = 'Confirmed'
    elif status_filter == 'Cancelled':
        query_filter['status'] = 'Cancelled'
    else:
        query_filter['status'] = status_filter

    # Auto-sync captured payments from Razorpay asynchronously in a background thread to eliminate dashboard load lag
    try:
        mongo_uri = current_app.config.get('MONGO_URI') or os.environ.get('MONGO_URI')
        if mongo_uri and not current_app.config.get('TESTING'):
            threading.Thread(
                target=_async_sync_payments,
                args=(mongo_uri, 20),
                daemon=True
            ).start()
    except Exception as se:
        logger.warning("Failed async auto-sync dispatch on dashboard load: %s", se)

    # Fetch bookings with filters (admin)
    bookings = list(db.bookings.find(query_filter).sort(sort_order).limit(500))
    # Sort by filtered date then by configured time-slot order (chronological)
    bookings.sort(key=lambda b: _booking_sort_key(b, time_slots))
    for booking in bookings:
        booking['created_at_ist'] = utc_to_ist(booking.get('created_at'))
        booking['is_admin_booking'] = bool(booking.get('is_admin_booking') or booking.get('booking_details', {}).get('is_admin_booking'))
        # Compute pricing and balance per booking for admin table / export
        total = booking.get('amount') or booking.get('total_amount')
        advance = booking.get('advance_amount')
        if advance is None or total is None:
            try:
                gs_val = int(booking.get('group_size', 0) or 0)
            except (ValueError, TypeError):
                gs_val = 0
            amt = calculate_total_amount(settings, booking.get('date'), gs_val)
            total = total if total is not None else amt.get('total_amount', 0)
            advance = advance if advance is not None else amt.get('advance_amount', 0)
            booking['advance_percent'] = amt.get('advance_percent', 0)
        else:
            booking['advance_percent'] = booking.get('advance_percent', 0)
        
        total_val = float(total if total is not None else 0)
        advance_val = float(advance if advance is not None else 0)
        booking['total_amount'] = total_val
        booking['advance_amount'] = advance_val
        paid = str(booking.get('payment_status', '')).lower() == 'paid'
        b_status = str(booking.get('status', '')).strip().lower()
        if b_status == 'visited':
            booking['payment_status_display'] = '—'
        else:
            booking['payment_status_display'] = 'Paid' if paid else 'Not paid'
        if booking.get('balance_amount') is not None:
            booking['balance_amount'] = float(booking.get('balance_amount'))
        else:
            booking['balance_amount'] = max(total_val - advance_val, 0.0) if paid else total_val

    range_slots = get_slots_for_date_range(db, from_date, to_date, settings)

    # Today's date for disabling Cancel/Postpone on past bookings
    today_str = datetime.date.today().isoformat()
    # Render dashboard with current booking filters (admin)
    response = make_response(render_template('admin_dashboard.html',
                         bookings=bookings,
                         is_subadmin=False,
                         settings=dashboard_settings,
                         all_slots=range_slots,
                         filter_from=from_date,
                         filter_to=to_date,
                         filter_slot=slot_filter,
                         filter_status=status_filter,
                         today_str=today_str))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@admin_bp.route('/api/export_bookings')
@login_required
@subadmin_or_admin_required
def api_export_bookings():
    """
    API endpoint to retrieve ALL matching bookings for PDF export,
    without the 500-booking limit applied to the admin dashboard display table.
    """
    try:
        db = current_app.mongo.db
        settings = load_settings(db)

        from_date = request.args.get('from', '').strip()
        to_date = request.args.get('to', '').strip()
        slot_filter = request.args.get('slot', '').strip()
        status_filter = request.args.get('status', '').strip()
        time_slots = get_slots_for_date_range(db, from_date, to_date, settings)

        query_filter = {}

        # Validate and build date range filter (identical to dashboard route)
        if from_date and to_date:
            try:
                f = datetime.date.fromisoformat(from_date)
                t = datetime.date.fromisoformat(to_date)
                if f <= t:
                    query_filter['date'] = {'$gte': from_date, '$lte': to_date}
            except (ValueError, TypeError):
                pass
        elif from_date:
            try:
                datetime.date.fromisoformat(from_date)
                query_filter['date'] = {'$gte': from_date}
            except (ValueError, TypeError):
                pass
        elif to_date:
            try:
                datetime.date.fromisoformat(to_date)
                query_filter['date'] = {'$lte': to_date}
            except (ValueError, TypeError):
                pass

        # If NO date filter is provided for Admin, default to TODAY (same as dashboard)
        if not from_date and not to_date:
            today_str = datetime.date.today().isoformat()
            query_filter['date'] = today_str

        # Slot filter
        if slot_filter:
            query_filter['slot'] = slot_filter

        # Status filter
        if not status_filter:
            query_filter['status'] = 'Confirmed'
        elif status_filter.lower() == 'all':
            pass
        elif status_filter.lower() == 'visited':
            query_filter['status'] = {'$in': ['Visited', 'visited']}
        elif status_filter in ['Pending', 'Confirmed', 'Cancelled']:
            query_filter['status'] = status_filter
        else:
            query_filter['status'] = status_filter

        sort_order = [('date', 1), ('created_at', -1)]
        # Fetch ALL matching bookings - NO limit!
        bookings = list(db.bookings.find(query_filter).sort(sort_order))
        bookings.sort(key=lambda b: _booking_sort_key(b, time_slots))

        export_data = []
        for b in bookings:
            total = b.get('amount') or b.get('total_amount')
            advance = b.get('advance_amount')
            if advance is None or total is None:
                try:
                    gs_val = int(b.get('group_size', 0) or 0)
                except (ValueError, TypeError):
                    gs_val = 0
                amt = calculate_total_amount(settings, b.get('date'), gs_val)
                total = total if total is not None else amt.get('total_amount', 0)
                advance = advance if advance is not None else amt.get('advance_amount', 0)
            
            paid = str(b.get('payment_status', '')).lower() == 'paid'
            advance_val = float(advance or 0)
            total_val = float(total or 0)
            if b.get('balance_amount') is not None:
                balance_val = float(b.get('balance_amount'))
            else:
                balance_val = max(total_val - advance_val, 0) if paid else total_val

            coupon_code = str(b.get('coupon_code') or b.get('booking_details', {}).get('coupon_code') or '')
            coupon_discount = float(b.get('coupon_discount_applied') or b.get('booking_details', {}).get('coupon_discount_applied') or 0)

            b_status_str = str(b.get('status') or '-').strip()
            if b_status_str.lower() == 'visited':
                p_status_display = '—'
                adv_display = '—'
                bal_display = '—'
            else:
                p_status_display = 'Paid' if paid else 'Not paid'
                adv_display = f"{advance_val:.2f}"
                bal_display = f"{balance_val:.2f}"

            export_data.append({
                'name': str(b.get('user_name') or b.get('name') or b.get('customer_name') or 'Unknown'),
                'phone': str(b.get('phone') or b.get('customer_phone') or '-'),
                'email': str(b.get('email') or b.get('customer_email') or '-'),
                'location': str(b.get('location') or '-'),
                'date': str(b.get('date') or '-'),
                'slot': str(b.get('slot') or '-'),
                'group_size': str(b.get('group_size', '-')),
                'payment_status': p_status_display,
                'advance': adv_display,
                'balance': bal_display,
                'status': b_status_str,
                'coupon_code': coupon_code,
                'coupon_discount': f"{coupon_discount:.2f}",
                'is_admin_booking': bool(b.get('is_admin_booking') or b.get('booking_details', {}).get('is_admin_booking'))
            })

        return jsonify({
            'success': True,
            'count': len(export_data),
            'bookings': export_data
        })
    except Exception as e:
        logger.error(f"Error exporting bookings for PDF: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/api/slots_for_date_range')
@login_required
@subadmin_or_admin_required
def api_slots_for_date_range():
    """
    Return valid slots for a given date or date range.
    Connected with the From and To date filters in the admin UI.
    """
    db = current_app.mongo.db
    settings = load_settings(db)
    from_date = request.args.get('from', '').strip()
    to_date = request.args.get('to', '').strip()

    if not from_date and not to_date:
        today_str = datetime.date.today().isoformat()
        from_date = today_str
        to_date = today_str

    slots = get_slots_for_date_range(db, from_date, to_date, settings)
    return jsonify({
        'success': True,
        'slots': slots,
        'from': from_date,
        'to': to_date
    })


@admin_bp.route('/calendar')
@login_required
@admin_required  # Only admin, not subadmin
def calendar():
    db = current_app.mongo.db
    settings = load_settings(db)
    
    # Use start_date and end_date from settings if available
    start_date_str = settings.get('start_date')
    end_date_str = settings.get('end_date')
    
    if start_date_str and end_date_str:
        try:
            # Use datetime.datetime.strptime when module 'datetime' is imported
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
            
            # Generate all dates in the range
            dates = []
            current = start_date
            while current <= end_date:
                dates.append(current.isoformat())
                current += datetime.timedelta(days=1)
        except (ValueError, TypeError):
            # Fallback to old calculation if date parsing fails
            days = settings.get('days', 30)
            today = datetime.date.today()
            dates = [(today + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    else:
        # Backward compatibility: calculate from days
        days = settings.get('days', 30)
        today = datetime.date.today()
        dates = [(today + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    
    # Optimize: Fetch all bookings in the date range in a single query
    calendar = {d: [] for d in dates}
    if dates:
        bookings = list(db.bookings.find({'date': {'$in': dates}}).sort('created_at', -1))
        for booking in bookings:
            date = booking.get('date')
            if date in calendar:
                calendar[date].append(booking)
    return render_template('admin_calendar.html', calendar=calendar, settings=settings)

@admin_bp.route('/bookings/<booking_id>/status', methods=['POST'])
@login_required
@admin_required  # Only admin, not subadmin
def change_status(booking_id):
    db = current_app.mongo.db
    status = request.form.get('status')
    raft_ids_raw = request.form.get('raft_ids','')
    raft_ids = []
    if raft_ids_raw:
        try:
            raft_ids = [int(x.strip()) for x in raft_ids_raw.split(',') if x.strip()]
        except:
            raft_ids = []
    update_booking_status(db, booking_id, status, raft_allocations=raft_ids if raft_ids else None)
    flash('Booking updated', 'success')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/settings', methods=['GET','POST'])
@login_required
@supreme_admin_required
def settings_page():
    db = current_app.mongo.db
    if request.method == 'POST':
        # Get old settings before updating
        old_settings = load_settings(db)
        
        # Parse and validate new settings
        try:
            from datetime import datetime, date
            
            # Get start_date and end_date from form
            start_date_str = request.form.get('start_date')
            end_date_str = request.form.get('end_date')
            days_from_form = request.form.get('days')  # Calculated by frontend
            
            # Validate date inputs
            if not start_date_str or not end_date_str:
                flash('Both start date and end date are required', 'error')
                return render_template('settings.html', settings=old_settings)
            
            # Parse dates
            try:
                start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            except ValueError:
                flash('Invalid date format. Please use YYYY-MM-DD format.', 'error')
                return render_template('settings.html', settings=old_settings)
            
            # Validate date range
            if end_date < start_date:
                flash('End date must be greater than or equal to start date.', 'error')
                return render_template('settings.html', settings=old_settings)
            
            # Calculate days: end_date - start_date + 1
            days = (end_date - start_date).days + 1
            
            # Validate calculated days matches frontend calculation (safety check)
            if days_from_form and int(days_from_form) != days:
                flash('Date range calculation mismatch. Please try again.', 'error')
                return render_template('settings.html', settings=old_settings)
            
            if days < 1:
                flash('Date range must be at least 1 day.', 'error')
                return render_template('settings.html', settings=old_settings)
            
            data = {
                '_id': 'system_settings',
                'start_date': start_date_str,
                'end_date': end_date_str,
                'days': days,
                'slots': int(request.form.get('slots')) if request.form.get('slots') else len(request.form.get('time_slots', '').split(',')),
                'rafts_per_slot': int(request.form.get('rafts_per_slot')),
                'capacity': int(request.form.get('capacity')),
                'time_slots': [s.strip() for s in request.form.get('time_slots').split(',') if s.strip()]
            }
            
            # Validate settings
            if data['rafts_per_slot'] < 1:
                flash('Rafts per slot must be at least 1', 'error')
                return render_template('settings.html', settings=old_settings)
            if data['capacity'] < 1:
                flash('Capacity per raft must be at least 1', 'error')
                return render_template('settings.html', settings=old_settings)
            if not data['time_slots']:
                flash('At least one time slot is required', 'error')
                return render_template('settings.html', settings=old_settings)
            
            # Parse amount settings (optional)
            weekday_amount_str = request.form.get('weekday_amount', '').strip()
            saturday_amount_str = request.form.get('saturday_amount', '').strip()
            weekday_advance_percent_str = request.form.get('weekday_advance_percent', '').strip()
            saturday_advance_percent_str = request.form.get('saturday_advance_percent', '').strip()
            platform_fee_str = request.form.get('platform_fee', '').strip()

            # Add amount settings to data if provided
            if weekday_amount_str:
                try:
                    weekday_amount = float(weekday_amount_str)
                    if weekday_amount < 0:
                        flash('Monday–Friday amount must be non-negative', 'error')
                        return render_template('settings.html', settings=old_settings)
                    data['weekday_amount'] = weekday_amount
                except ValueError:
                    flash('Monday–Friday amount must be a valid number', 'error')
                    return render_template('settings.html', settings=old_settings)

            if saturday_amount_str:
                try:
                    saturday_amount = float(saturday_amount_str)
                    if saturday_amount < 0:
                        flash('Saturday amount must be non-negative', 'error')
                        return render_template('settings.html', settings=old_settings)
                    data['saturday_amount'] = saturday_amount
                except ValueError:
                    flash('Saturday amount must be a valid number', 'error')
                    return render_template('settings.html', settings=old_settings)

            # Add advance percentage settings to data if provided
            if weekday_advance_percent_str:
                try:
                    weekday_advance_percent = float(weekday_advance_percent_str)
                    if weekday_advance_percent < 0 or weekday_advance_percent > 100:
                        flash('Weekday advance percent must be between 0 and 100', 'error')
                        return render_template('settings.html', settings=old_settings)
                    data['weekday_advance_percent'] = weekday_advance_percent
                except ValueError:
                    flash('Weekday advance percent must be a valid number', 'error')
                    return render_template('settings.html', settings=old_settings)

            if saturday_advance_percent_str:
                try:
                    saturday_advance_percent = float(saturday_advance_percent_str)
                    if saturday_advance_percent < 0 or saturday_advance_percent > 100:
                        flash('Weekend advance percent must be between 0 and 100', 'error')
                        return render_template('settings.html', settings=old_settings)
                    data['saturday_advance_percent'] = saturday_advance_percent
                except ValueError:
                    flash('Weekend advance percent must be a valid number', 'error')
                    return render_template('settings.html', settings=old_settings)

            # Add platform fee percentage settings to data if provided
            if platform_fee_str:
                try:
                    platform_fee = float(platform_fee_str)
                    if platform_fee < 0 or platform_fee > 100:
                        flash('Platform fee percentage must be between 0 and 100', 'error')
                        return render_template('settings.html', settings=old_settings)
                    data['platform_fee'] = platform_fee
                except ValueError:
                    flash('Platform fee percentage must be a valid number', 'error')
                    return render_template('settings.html', settings=old_settings)
            else:
                # Set default platform fee to 0 if not provided
                data['platform_fee'] = 0
            
        except (ValueError, TypeError) as e:
            flash(f'Invalid input: {str(e)}', 'error')
            settings = load_settings(db)
            return render_template('settings.html', settings=settings)
        
        # Save new settings to database
        db.settings.replace_one({'_id':'system_settings'}, data, upsert=True)
        
        # Invalidate cache and refresh with new settings
        invalidate_settings_cache(current_app)
        refresh_settings_cache(current_app, db)
        
        # Determine if raft regeneration is needed
        raft_regen_needed = (old_settings.get('rafts_per_slot', 5) != data.get('rafts_per_slot', 5) or
                            set(old_settings.get('time_slots', [])) != set(data.get('time_slots', [])))
        
        # Build success message - show immediately without waiting for raft regeneration
        messages = ['✅ Settings updated successfully!']
        
        if raft_regen_needed:
            messages.append('🔄 Regenerating rafts in background...')
            # Start background thread for raft regeneration (non-blocking)
            # Pass MONGO_URI to avoid threading context issues
            mongo_uri = current_app.config.get('MONGO_URI') or os.environ.get('MONGO_URI')
            thread = threading.Thread(
                target=_regenerate_rafts_async,
                args=(mongo_uri, old_settings, data),
                daemon=True
            )
            thread.start()
            print("[SETTINGS] Background raft regeneration thread started (non-blocking)")
        else:
            # Only capacity/amount settings changed - this is instant
            if old_settings.get('capacity', 6) != data.get('capacity', 6):
                messages.append('📊 Raft capacity updated.')
            if old_settings.get('weekday_amount') != data.get('weekday_amount'):
                messages.append('💵 Weekday amount updated.')
            if old_settings.get('saturday_amount') != data.get('saturday_amount'):
                messages.append('💵 Weekend amount updated.')
            if old_settings.get('platform_fee') != data.get('platform_fee'):
                messages.append('🏦 Platform fee updated.')
        
        return render_template('settings.html', settings=data, message=' | '.join(messages))
    
    # GET request - load current settings
    settings = load_settings(db)
    return render_template('settings.html', settings=settings)
# Occupancy endpoints (filter by day param)
from datetime import date as _date

@admin_bp.route('/api/settings', methods=['GET'])
@login_required
@admin_required
def api_get_settings():
    """API endpoint to get fresh settings for frontend refresh."""
    db = current_app.mongo.db
    settings = load_settings(db)
    # Refresh cache
    refresh_settings_cache(current_app, db)
    return jsonify(settings)

@admin_bp.route('/delete_bookings_by_date', methods=['DELETE'])
@login_required
@supreme_admin_required
def delete_bookings_by_date():
    """Delete all bookings for a specific date and free up raft occupancy."""
    db = current_app.mongo.db
    date = request.args.get('date')
    
    if not date:
        return jsonify({'error': 'Date parameter is required'}), 400
    
    try:
        # Validate date format
        from datetime import datetime
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    
    # Find all bookings for the date
    bookings = list(db.bookings.find({'date': date}))
    
    if not bookings:
        return jsonify({'message': f'No bookings found for {date}'}), 200
    
    # Free up raft occupancy for confirmed bookings using allocation pattern logic
    from utils.booking_ops import cancel_booking, get_deallocation_amounts
    from bson.objectid import ObjectId
    
    freed_count = 0
    for booking in bookings:
        if booking.get('status') == 'Confirmed' and booking.get('raft_allocations'):
            # Use the same deallocation logic as cancel_booking
            raft_ids = booking.get('raft_allocations', [])
            group_size = int(booking.get('group_size', 0))
            booking_date = booking.get('date')
            booking_slot = booking.get('slot')
            
            if raft_ids and group_size > 0:
                # Get deallocation amounts using allocation pattern logic
                deallocations = get_deallocation_amounts(db, booking_date, booking_slot, group_size, raft_ids)
                
                # Apply deallocation to each raft following allocation pattern rules
                for raft_id, amount_to_remove in deallocations:
                    raft = db.rafts.find_one({'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id})
                    if not raft:
                        continue
                    
                    current_occupancy = max(0, raft.get('occupancy', 0))
                    new_occupancy = max(0, current_occupancy - amount_to_remove)
                    
                    # Build update following allocation pattern rules
                    update_data = {'$set': {'occupancy': new_occupancy}}
                    
                    # Clear is_special flag following allocation logic rules
                    if new_occupancy == 0:
                        update_data['$set']['is_special'] = False
                    elif new_occupancy != 7:
                        update_data['$set']['is_special'] = False
                    
                    db.rafts.update_one(
                        {'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id},
                        update_data
                    )
                
                freed_count += 1
    
    # Delete associated payments
    booking_ids = [str(b['_id']) for b in bookings]
    db.payments.delete_many({'booking_id': {'$in': booking_ids}})
    
    # Delete all bookings for the date
    result = db.bookings.delete_many({'date': date})
    deleted_count = result.deleted_count
    
    # Double-check: If no bookings exist for this date, delete all raft records for this date
    # This ensures a completely clean slate for the date.
    remaining_bookings = db.bookings.count_documents({'date': date})
    if remaining_bookings == 0:
        db.rafts.delete_many({'day': date})
    else:
        # Clean up occupancy for remaining bookings (safety)
        db.rafts.update_many(
            {'day': date, 'occupancy': {'$lt': 0}},
            {'$set': {'occupancy': 0, 'is_special': False}}
        )
        db.rafts.update_many(
            {'day': date, '$or': [{'occupancy': {'$lte': 0}}, {'occupancy': {'$exists': False}}]},
            {'$set': {'occupancy': 0, 'is_special': False}}
        )
    
    return jsonify({
        'message': f'Successfully deleted {deleted_count} booking(s) for {date}. Freed occupancy from {freed_count} confirmed booking(s).',
        'deleted_count': deleted_count,
        'freed_count': freed_count
    }), 200


@admin_bp.route('/delete_records_by_date_range', methods=['POST'])
@login_required
@supreme_admin_required
def delete_records_by_date_range():
    """Delete bookings within a date range (inclusive) with optional status filter for customer bookings.
    Admin booking history within the date range is always deleted regardless of customer status filter.
    Supreme Admin only.

    Request JSON: { "from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "status": "all|Confirmed|Pending|Cancelled|Visited" }
    Returns JSON with deleted_count, customer_deleted_count, admin_deleted_count, freed_count, and message.
    """
    db = current_app.mongo.db
    data = request.get_json() or {}
    from_date = (data.get('from') or '').strip()
    to_date = (data.get('to') or '').strip()
    status_filter = data.get('status', 'all')

    # Validate presence
    if not from_date or not to_date:
        return jsonify({'error': 'Both from and to dates are required'}), 400

    # Validate format
    try:
        from datetime import datetime as _dt
        f = _dt.strptime(from_date, '%Y-%m-%d').date()
        t = _dt.strptime(to_date, '%Y-%m-%d').date()
    except Exception:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

    # Validate range
    if f > t:
        return jsonify({'error': 'From Date must not be later than To Date'}), 400

    # Enforce system start/end dates
    settings = load_settings(db)
    sys_start = settings.get('start_date')
    sys_end = settings.get('end_date')
    try:
        if sys_start:
            from datetime import datetime as _dt
            sd = _dt.strptime(sys_start, '%Y-%m-%d').date()
            if f < sd:
                return jsonify({'error': f'From date cannot be earlier than system start date {sys_start}'}), 400
        if sys_end:
            from datetime import datetime as _dt
            ed = _dt.strptime(sys_end, '%Y-%m-%d').date()
            if t > ed:
                return jsonify({'error': f'To date cannot be later than system end date {sys_end}'}), 400
    except Exception:
        # If settings dates malformed, deny to be safe
        return jsonify({'error': 'System date configuration invalid; aborting deletion'}), 400

    # Build consistent date range condition covering start and end dates inclusively
    date_range_condition = {
        '$or': [
            {'date': {'$gte': from_date, '$lte': to_date}},
            {
                '$and': [
                    {'date': {'$exists': False}},
                    {'booking_details.date': {'$gte': from_date, '$lte': to_date}}
                ]
            }
        ]
    }

    # Customer booking query: not marked as admin booking, within date range
    customer_query = {
        '$and': [
            {
                '$nor': [
                    {'is_admin_booking': True},
                    {'booking_details.is_admin_booking': True}
                ]
            },
            date_range_condition
        ]
    }
    if status_filter != 'all':
        customer_query['$and'].append({
            'status': {'$in': [status_filter, status_filter.capitalize(), status_filter.lower(), status_filter.upper()]}
        })

    # Admin booking history query: marked as admin booking, within date range (status filter is not applied)
    admin_query = {
        '$and': [
            {
                '$or': [
                    {'is_admin_booking': True},
                    {'booking_details.is_admin_booking': True}
                ]
            },
            date_range_condition
        ]
    }

    try:
        customer_bookings = list(db.bookings.find(customer_query))
        admin_bookings = list(db.bookings.find(admin_query))
    except Exception as e:
        logger.error(f"Error querying bookings for deletion: {e}", exc_info=True)
        return jsonify({'error': f'Database query error: {str(e)}'}), 500

    if not customer_bookings and not admin_bookings:
        status_msg = f" with status '{status_filter}'" if status_filter != 'all' else ""
        return jsonify({
            'message': f'No matching customer or admin bookings found between {from_date} and {to_date}{status_msg}.',
            'deleted_count': 0,
            'customer_deleted_count': 0,
            'admin_deleted_count': 0,
            'freed_count': 0
        }), 200

    all_bookings_to_delete = customer_bookings + admin_bookings
    customer_ids = [b['_id'] for b in customer_bookings]
    admin_ids = [b['_id'] for b in admin_bookings]
    all_booking_ids = customer_ids + admin_ids
    all_booking_id_strs = [str(bid) for bid in all_booking_ids]

    # Use booking_ops deallocation to free raft occupancy for confirmed bookings being deleted
    from utils.booking_ops import get_deallocation_amounts
    freed_count = 0

    try:
        for booking in all_bookings_to_delete:
            b_status = str(booking.get('status', '')).strip().title()
            if b_status == 'Confirmed' and booking.get('raft_allocations'):
                raft_ids = booking.get('raft_allocations', [])
                try:
                    group_size = int(booking.get('group_size', 0) or 0)
                except (ValueError, TypeError):
                    group_size = 0
                booking_date = booking.get('date') or booking.get('booking_details', {}).get('date')
                booking_slot = booking.get('slot') or booking.get('booking_details', {}).get('slot')
                if raft_ids and group_size > 0 and booking_date and booking_slot:
                    try:
                        deallocations = get_deallocation_amounts(db, booking_date, booking_slot, group_size, raft_ids)
                        for raft_id, amount_to_remove in deallocations:
                            raft = db.rafts.find_one({'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id})
                            if not raft:
                                continue
                            current_occupancy = max(0, raft.get('occupancy', 0))
                            new_occupancy = max(0, current_occupancy - amount_to_remove)
                            update_data = {'$set': {'occupancy': new_occupancy}}
                            if new_occupancy == 0:
                                update_data['$set']['is_special'] = False
                            elif new_occupancy != 7:
                                update_data['$set']['is_special'] = False
                            db.rafts.update_one({'day': booking_date, 'slot': booking_slot, 'raft_id': raft_id}, update_data)
                        freed_count += 1
                    except Exception as alloc_err:
                        logger.warning(f"Failed to deallocate raft for booking {booking.get('_id')}: {alloc_err}")
                        continue

        # Delete associated payments
        if all_booking_id_strs:
            db.payments.delete_many({'booking_id': {'$in': all_booking_id_strs + all_booking_ids}})

        # Delete customer bookings
        customer_deleted_count = 0
        if customer_ids:
            res_customer = db.bookings.delete_many({'_id': {'$in': customer_ids}})
            customer_deleted_count = res_customer.deleted_count

        # Delete admin bookings
        admin_deleted_count = 0
        if admin_ids:
            res_admin = db.bookings.delete_many({'_id': {'$in': admin_ids}})
            admin_deleted_count = res_admin.deleted_count

        total_deleted_count = customer_deleted_count + admin_deleted_count

        # Post-cleanup of rafts (safety check)
        remaining_count = db.bookings.count_documents({'date': {'$gte': from_date, '$lte': to_date}})
        if remaining_count == 0:
            db.rafts.delete_many({'day': {'$gte': from_date, '$lte': to_date}})
        else:
            db.rafts.update_many({'day': {'$gte': from_date, '$lte': to_date}, 'occupancy': {'$lt': 0}}, {'$set': {'occupancy': 0, 'is_special': False}})
            db.rafts.update_many({'day': {'$gte': from_date, '$lte': to_date}, '$or': [{'occupancy': {'$lte': 0}}, {'occupancy': {'$exists': False}}]}, {'$set': {'occupancy': 0, 'is_special': False}})

    except Exception as e:
        logger.error(f"Database error during deletion operation: {e}", exc_info=True)
        return jsonify({'error': f'Database error during deletion: {str(e)}'}), 500

    # Audit log
    try:
        admin_id = None
        try:
            admin_id = current_user.get_id()
        except Exception:
            admin_id = getattr(current_user, 'id', None) or getattr(current_user, '_id', None) or getattr(current_user, 'email', None)
        audit = {
            'action': 'delete_records_by_date_range',
            'admin_id': str(admin_id),
            'admin_repr': getattr(current_user, 'email', '') or getattr(current_user, 'username', '') or str(admin_id),
            'from_date': from_date,
            'to_date': to_date,
            'status_filter': status_filter,
            'customer_deleted_count': customer_deleted_count,
            'admin_deleted_count': admin_deleted_count,
            'deleted_count': total_deleted_count,
            'freed_confirmed_bookings': freed_count,
            'timestamp': datetime.datetime.now(timezone.utc)
        }
        db.admin_audit_logs.insert_one(audit)
    except Exception:
        pass

    parts = []
    if customer_deleted_count > 0:
        status_part = f" with status '{status_filter}'" if status_filter != 'all' else ""
        parts.append(f"{customer_deleted_count} customer booking(s){status_part}")
    if admin_deleted_count > 0:
        parts.append(f"{admin_deleted_count} admin booking(s)")

    if parts:
        breakdown_msg = " and ".join(parts)
        message = f"Successfully deleted {breakdown_msg} between {from_date} and {to_date}. Freed occupancy from {freed_count} confirmed booking(s)."
    else:
        status_msg = f" with status '{status_filter}'" if status_filter != 'all' else ""
        message = f"No matching customer or admin bookings found between {from_date} and {to_date}{status_msg}."

    return jsonify({
        'message': message,
        'deleted_count': total_deleted_count,
        'customer_deleted_count': customer_deleted_count,
        'admin_deleted_count': admin_deleted_count,
        'freed_count': freed_count
    }), 200

@admin_bp.route('/create_immediate_slot', methods=['POST'])
@login_required
@supreme_admin_required
def create_immediate_slot():
    """Create an immediate slot for a specific date (Supreme Admin only)."""
    db = current_app.mongo.db
    data = request.get_json(silent=True) or request.form
    date_str = (data.get('date') or '').strip()
    slot_str = (data.get('slot') or '').strip()

    if not date_str:
        return jsonify({'success': False, 'error': 'Particular date is required.'}), 400
    if not slot_str:
        return jsonify({'success': False, 'error': 'Slot name is required.'}), 400

    try:
        from datetime import datetime as _dt
        _dt.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    slot_clean = " ".join(slot_str.split())

    settings = load_settings(db)
    regular_slots = [s.strip().lower() for s in settings.get('time_slots', [])]
    if slot_clean.lower() in regular_slots:
        return jsonify({
            'success': False,
            'error': f"'{slot_clean}' is already a regular daily time slot in System Settings."
        }), 400

    existing = db.immediate_slots.find_one({
        'date': date_str,
        'slot': {'$regex': f'^{slot_clean}$', '$options': 'i'}
    })
    if existing:
        return jsonify({
            'success': False,
            'error': f"Immediate slot '{slot_clean}' already exists for {date_str}."
        }), 400

    creator = getattr(current_user, 'email', '') or str(current_user.get_id())
    record = {
        'date': date_str,
        'slot': slot_clean,
        'created_at': datetime.datetime.now(timezone.utc),
        'created_by': creator
    }
    db.immediate_slots.insert_one(record)

    from models.raft_model import ensure_rafts_for_date_slot
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)
    ensure_rafts_for_date_slot(db, date_str, slot_clean, rafts_per_slot, capacity)

    invalidate_settings_cache(current_app)

    try:
        db.admin_audit_logs.insert_one({
            'action': 'create_immediate_slot',
            'admin': creator,
            'date': date_str,
            'slot': slot_clean,
            'timestamp': datetime.datetime.now(timezone.utc)
        })
    except Exception:
        pass

    msg = f"Immediate slot '{slot_clean}' successfully created for {date_str} with {rafts_per_slot} rafts ({capacity} seats each)!"
    return jsonify({
        'success': True,
        'message': msg,
        'slot': {
            'date': date_str,
            'slot': slot_clean,
            'created_by': creator,
            'rafts_per_slot': rafts_per_slot,
            'capacity': capacity
        }
    }), 200


@admin_bp.route('/immediate_slots', methods=['GET'])
@login_required
@supreme_admin_required
def get_immediate_slots():
    """List configured immediate slots with active booking counts (Supreme Admin only)."""
    db = current_app.mongo.db
    qdate = request.args.get('date', '').strip()
    query = {}
    if qdate:
        query['date'] = qdate

    slots_cursor = list(db.immediate_slots.find(query).sort([('date', 1), ('slot', 1)]))
    results = []
    for s in slots_cursor:
        d = s.get('date')
        slot_name = s.get('slot')
        active_count = db.bookings.count_documents({
            'date': d,
            'slot': slot_name,
            'status': {'$in': ['Confirmed', 'Pending', 'Visited']}
        })
        created_at_val = s.get('created_at')
        created_str = utc_to_ist(created_at_val) if created_at_val else ''
        results.append({
            'id': str(s['_id']),
            'date': d,
            'slot': slot_name,
            'bookings_count': active_count,
            'created_by': s.get('created_by', ''),
            'created_at': created_str
        })

    settings = load_settings(db)
    default_slots = list(settings.get('time_slots', []))

    return jsonify({
        'success': True,
        'slots': results,
        'default_slots': default_slots,
        'date': qdate
    })


@admin_bp.route('/delete_immediate_slot', methods=['POST'])
@login_required
@supreme_admin_required
def delete_immediate_slot():
    """Delete an immediate slot if it has no active bookings (Supreme Admin only)."""
    db = current_app.mongo.db
    data = request.get_json(silent=True) or request.form
    slot_id = data.get('id')
    date_str = (data.get('date') or '').strip()
    slot_str = (data.get('slot') or '').strip()

    record = None
    if slot_id:
        try:
            record = db.immediate_slots.find_one({'_id': ObjectId(slot_id)})
        except Exception:
            pass
    if not record and date_str and slot_str:
        record = db.immediate_slots.find_one({'date': date_str, 'slot': slot_str})

    if not record:
        return jsonify({'success': False, 'error': 'Immediate slot record not found.'}), 404

    d = record.get('date')
    s = record.get('slot')

    active_count = db.bookings.count_documents({
        'date': d,
        'slot': s,
        'status': {'$in': ['Confirmed', 'Pending', 'Visited']}
    })
    if active_count > 0:
        return jsonify({
            'success': False,
            'error': f"Cannot delete slot '{s}' on {d} because it has {active_count} active booking(s)."
        }), 400

    db.immediate_slots.delete_one({'_id': record['_id']})

    any_bookings = db.bookings.count_documents({'date': d, 'slot': s})
    if any_bookings == 0:
        db.rafts.delete_many({'day': d, 'slot': s})

    invalidate_settings_cache(current_app)

    return jsonify({
        'success': True,
        'message': f"Immediate slot '{s}' on {d} deleted successfully."
    }), 200


@admin_bp.route('/occupancy_data')
@login_required
@subadmin_or_admin_required
def occupancy_data():
    from datetime import date as _date
    from models.raft_model import ensure_rafts_for_date_slot
    db = current_app.mongo.db
    settings = load_settings(db)
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)
    
    # Get day parameter
    qday = request.args.get('day')
    
    # Sub-Admin: Only allow single date selection, default to today if not provided
    if current_user.is_subadmin():
        if not qday:
            qday = _date.today().isoformat()
        # Validate that the date is a valid date string (security check)
        try:
            _date.fromisoformat(qday)
        except (ValueError, TypeError):
            qday = _date.today().isoformat()
        allowed_dates = [qday]
    else:
        # Admin: Use provided day parameter or default to today
        if not qday:
            qday = _date.today().isoformat()
        allowed_dates = [qday]
    
    qday = allowed_dates[0]  # Single date for both admin and subadmin
    slots = get_slots_for_date(db, qday, settings)

    # Ensure rafts exist for all slots with current settings for allowed dates
    for date_str in allowed_dates:
        for slot in slots:
            ensure_rafts_for_date_slot(db, date_str, slot, rafts_per_slot, capacity)
    
    result = {}
    
    # For both admin and subadmin, return data grouped by slot (single date)
    for slot in slots:
        # Fetch only the configured number of rafts (limit to rafts_per_slot)
        rafts = list(db.rafts.find({'slot': slot, 'day': qday}).sort('raft_id', 1).limit(rafts_per_slot))
        # Clamp occupancy to >= 0 and ensure is_special is only True if occupancy > 0
        result[slot] = [{
            'raft_id': r.get('raft_id', '?'), 
            'occupancy': max(0, r.get('occupancy', 0)), 
            'capacity': capacity,
            'is_special': r.get('is_special', False) and max(0, r.get('occupancy', 0)) > 0
        } for r in rafts[:rafts_per_slot]]
    return jsonify(result)

@admin_bp.route('/occupancy_by_date')
@login_required
@admin_required
def occupancy_by_date():
    from models.raft_model import ensure_rafts_for_date_slot
    db = current_app.mongo.db
    settings = load_settings(db)
    rafts_per_slot = settings.get('rafts_per_slot', 5)
    capacity = settings.get('capacity', 6)
    qday = request.args.get('day')
    rafts_query = {'day': qday} if qday else {}
    
    # If a specific day is requested, ensure rafts exist for all slots
    if qday:
        slots = get_slots_for_date(db, qday, settings)
        for slot in slots:
            ensure_rafts_for_date_slot(db, qday, slot, rafts_per_slot, capacity)
    
    rafts = list(db.rafts.find(rafts_query).sort([('day',1), ('slot',1), ('raft_id',1)]))
    grouped = {}
    for r in rafts:
        day = r.get('day', 'Unknown')
        slot = r.get('slot', 'Unknown')
        
        # Initialize day and slot in grouped if not exists
        if day not in grouped:
            grouped[day] = {}
        if slot not in grouped[day]:
            grouped[day][slot] = []
        
        # Only add rafts up to the configured limit per slot
        if len(grouped[day][slot]) < rafts_per_slot:
            grouped[day][slot].append({
                'raft_id': r.get('raft_id', '?'),
                'occupancy': max(0, r.get('occupancy', 0)),
                'capacity': capacity
            })
    return jsonify(grouped)

@admin_bp.route('/occupancy_detail')
@login_required
@subadmin_or_admin_required
def occupancy_detail():
    from datetime import date as _date, timedelta

    try:
        db = current_app.mongo.db
        settings = load_settings(db)
        slots = settings.get('time_slots', [])
        rafts_per_slot = settings.get('rafts_per_slot', 5)
        capacity = settings.get('capacity', 6)

        # Get from/to parameters (date range)
        from_date = request.args.get('from', '').strip()
        to_date = request.args.get('to', '').strip()

        # Default both empty -> today
        if not from_date and not to_date:
            today = _date.today().isoformat()
            from_date = today
            to_date = today

        # Validate dates
        try:
            f = _date.fromisoformat(from_date)
            t = _date.fromisoformat(to_date)
        except (ValueError, TypeError) as ex:
            return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400

        # Validate range
        if f > t:
            return jsonify({'error': 'From Date must not be later than To Date'}), 400

        # Build list of allowed dates (inclusive)
        allowed_dates = []
        cur = f
        while cur <= t:
            allowed_dates.append(cur.isoformat())
            cur = cur + timedelta(days=1)

        # Ensure rafts exist for standard slots with current settings for allowed dates
        from models.raft_model import ensure_rafts_for_dates_and_slots
        ensure_rafts_for_dates_and_slots(db, allowed_dates, slots, rafts_per_slot, capacity)

        # Batch fetch immediate slots for all allowed dates
        imm_records = list(db.immediate_slots.find({'date': {'$in': allowed_dates}}))
        imm_by_date = {}
        for imm in imm_records:
            imm_d = imm.get('date', '').strip()
            imm_s = imm.get('slot', '').strip()
            if imm_d and imm_s:
                imm_by_date.setdefault(imm_d, []).append(imm_s)

        # Pre-fetch all rafts into memory for allowed dates
        all_rafts = list(db.rafts.find({
            'day': {'$in': allowed_dates}
        }).sort('raft_id', 1))
        
        rafts_by_date_slot = {}
        for r in all_rafts:
            d = r.get('day')
            s = r.get('slot')
            rafts_by_date_slot.setdefault(d, {}).setdefault(s, []).append(r)

        # Prepare bookings_by_slot_by_date (only for display of booking details related to rafts)
        bookings_by_slot = {}
        # Only show bookings that are actually confirmed; pending / cancelled
        # bookings should not be counted in the occupancy overview.
        for b in db.bookings.find(
            {
                'date': {'$gte': from_date, '$lte': to_date},
                'status': 'Confirmed'
            }
        ):
            s = b.get('slot')
            date_key = b.get('date')
            bookings_by_slot.setdefault(date_key, {}).setdefault(s, []).append(b)

        # Build result grouped by date -> slot -> raft_list
        # IMPORTANT: Occupancy must be derived **only from confirmed bookings**,
        # not from raw raft occupancy, so that "pending" / unpaid bookings do not
        # appear as booked seats in the admin Occupancy Overview.
        result = {}
        base_slots = list(settings.get('time_slots', []))
        for date_str in allowed_dates:
            result[date_str] = {}
            date_slots = list(base_slots)
            for imm_slot in imm_by_date.get(date_str, []):
                if imm_slot not in date_slots:
                    date_slots.append(imm_slot)
            if date_str in bookings_by_slot:
                for bs in bookings_by_slot[date_str].keys():
                    if bs and bs not in date_slots:
                        date_slots.append(bs)
            date_slots.sort(key=_parse_time_slot)
            for slot in date_slots:
                # Fetch ALL rafts for this date/slot (not just configured ones)
                # We'll mark extras as "preserved" if they have occupancy
                all_rafts_for_slot = rafts_by_date_slot.get(date_str, {}).get(slot, [])

                # All confirmed bookings for this date+slot (prepared above)
                slot_bookings = bookings_by_slot.get(date_str, {}).get(slot, [])

                # Compute confirmed occupancy per raft_id from bookings only.
                # This ensures that "pending" bookings (which may still have
                # provisional raft allocations) do NOT contribute to occupancy.
                occupancy_by_raft = {}
                for b in slot_bookings:
                    group_size = int(b.get('group_size', 0) or 0)
                    if group_size <= 0:
                        continue

                    details = b.get('raft_allocation_details') or []
                    allocations = b.get('raft_allocations') or []

                    if details:
                        # Preferred path: we know exactly how many seats were placed in each raft
                        for entry in details:
                            try:
                                rid = int(entry.get('raft_id'))
                                count = int(entry.get('count', 0) or 0)
                            except Exception:
                                continue
                            if count <= 0:
                                continue
                            occupancy_by_raft[rid] = occupancy_by_raft.get(rid, 0) + count
                    elif allocations:
                        # Fallback: distribute group_size evenly across allocated rafts
                        try:
                            raft_ids = [int(rid) for rid in allocations]
                        except Exception:
                            continue
                        if not raft_ids:
                            continue
                        per = group_size // len(raft_ids)
                        rem = group_size % len(raft_ids)
                        for idx, rid in enumerate(raft_ids):
                            add = per + (1 if idx < rem else 0)
                            if add <= 0:
                                continue
                            occupancy_by_raft[rid] = occupancy_by_raft.get(rid, 0) + add

                # Build raft list using computed confirmed occupancy
                # Include ALL rafts, marking preserved ones
                raft_list = []
                for idx, r in enumerate(all_rafts_for_slot):
                    rid = r.get('raft_id')
                    try:
                        rid_int = int(rid)
                    except Exception:
                        rid_int = None

                    confirmed_occupancy = max(
                        0,
                        occupancy_by_raft.get(rid_int, 0) if rid_int is not None else 0,
                    )

                    # Mark special rafts only when they actually hold 7 confirmed seats
                    is_special = confirmed_occupancy == 7
                    
                    # Check if this raft is beyond the configured rafts_per_slot
                    # and has occupancy (meaning it's preserved due to bookings)
                    is_preserved = (idx >= rafts_per_slot) and (confirmed_occupancy > 0)
                    preserved_reason = "🛡️ Hidden (preserved due to bookings)" if is_preserved else None

                    raft_bookings = []
                    for b in slot_bookings:
                        if b.get('raft_allocations') and rid_int in b.get('raft_allocations', []):
                            raft_bookings.append({
                                'id': str(b['_id']),
                                'name': b.get('user_name') or b.get('name') or '',
                                'email': b.get('email',''),
                                'group_size': b.get('group_size'),
                                'status': b.get('status')
                            })

                    raft_list.append({
                        'raft_id': rid,
                        'occupancy': confirmed_occupancy,
                        'capacity': capacity,
                        'is_special': is_special,
                        'is_preserved': is_preserved,
                        'preserved_reason': preserved_reason,
                        'bookings': raft_bookings
                    })

                result[date_str][slot] = raft_list

        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f'[ERROR] occupancy_detail: {str(e)}')
        return jsonify({'error': 'Server error fetching occupancy'}), 500

@admin_bp.route('/cancel_booking/<booking_id>', methods=['POST'])
@login_required
@admin_required  # Only admin, not subadmin
def cancel_booking_route(booking_id):
    db = current_app.mongo.db
    try:
        oid = ObjectId(booking_id)
    except Exception:
        return jsonify({'error': 'Invalid booking id'}), 400
    
    # Get booking details before cancellation
    booking_before_cancel = db.bookings.find_one({'_id': oid})
    if booking_before_cancel and (booking_before_cancel.get('status') == 'Pending' or 'Pending' in str(booking_before_cancel.get('status'))):
        return jsonify({'error': 'Pending bookings cannot be cancelled directly. Please use the Rebook button.'}), 400
    
    print('Cancel called for', booking_id)
    res = cancel_booking(db, oid)
    
    # If cancellation successful, send email to user
    if 'error' not in res and booking_before_cancel:
        try:
            from utils.email_service import send_cancellation_email, send_email_async
            send_email_async(send_cancellation_email, booking_before_cancel)
            print(f"[ADMIN-CANCEL] Cancellation email dispatched asynchronously to {booking_before_cancel.get('email')}")
        except Exception as e:
            print(f"[ADMIN-CANCEL] Error sending cancellation email: {e}")
            # Don't fail the cancellation if email fails, just log the error
    
    return jsonify(res)

@admin_bp.route('/reduce_group_size/<booking_id>', methods=['POST'])
@login_required
@admin_required
def reduce_group_size_route(booking_id):
    db = current_app.mongo.db
    data = request.get_json() or {}
    new_size_str = data.get('new_size')
    
    if new_size_str is None:
        return jsonify({'error': 'new_size required'}), 400
        
    try:
        new_size = int(new_size_str)
    except ValueError:
        return jsonify({'error': 'Invalid new_size'}), 400
        
    try:
        oid = ObjectId(booking_id)
    except Exception:
        return jsonify({'error': 'Invalid booking id'}), 400
    
    if new_size <= 0:
        return jsonify({'error': 'Completely reducing group size to 0 is not allowed here. To cancel the booking, please use the Cancel button.'}), 400
        
    booking_before_resize = db.bookings.find_one({'_id': oid})
    old_size = int(booking_before_resize.get('group_size', 0)) if booking_before_resize else 0

    from utils.booking_ops import reduce_booking_group_size
    res = reduce_booking_group_size(db, oid, new_size)

    if 'error' not in res and booking_before_resize:
        try:
            updated_booking = db.bookings.find_one({'_id': oid})
            from utils.email_service import send_group_size_edit_email, send_email_async
            send_email_async(send_group_size_edit_email, updated_booking, old_size)
            print(f"[ADMIN-RESIZE] Edit size email dispatched asynchronously to {updated_booking.get('email')}")
        except Exception as e:
            print(f"[ADMIN-RESIZE] Error sending edit size email: {e}")

    return jsonify(res)


@admin_bp.route('/rebook_pending/<booking_id>', methods=['POST'])
@login_required
@admin_required
def rebook_pending_route(booking_id):
    db = current_app.mongo.db
    try:
        oid = ObjectId(booking_id)
    except Exception:
        return jsonify({'error': 'Invalid booking id'}), 400
        
    booking = db.bookings.find_one({'_id': oid})
    if not booking:
        return jsonify({'error': 'Booking not found'}), 404
        
    if booking.get('status') != 'Pending':
        return jsonify({'error': 'Only pending bookings can be manually rebooked.'}), 400
        
    data = request.get_json(silent=True) or request.form or {}
    new_date = (data.get('new_date') or '').strip() or booking.get("date") or booking.get("booking_details", {}).get("date")
    new_slot = (data.get('new_slot') or '').strip() or booking.get("slot") or booking.get("booking_details", {}).get("slot")
    
    if not new_date or not new_slot:
        return jsonify({'error': 'Target date and time slot are required for rebooking.'}), 400

    try:
        group_size = int(
            booking.get("group_size")
            or booking.get("booking_details", {}).get("group_size")
            or 0
        )
    except (TypeError, ValueError):
        group_size = 0

    old_date = booking.get("date") or booking.get("booking_details", {}).get("date")
    old_slot = booking.get("slot") or booking.get("booking_details", {}).get("slot")

    from utils.allocation_logic import load_settings, allocate_raft
    from utils.booking_ops import check_capacity_available, deallocate_amount_from_booking, recompute_occupancy_for_slot
    from models.raft_model import ensure_rafts_for_date_slot
    
    settings = load_settings(db)
    ensure_rafts_for_date_slot(db, new_date, new_slot, settings.get('rafts_per_slot', 5), settings.get('capacity', 6))

    # Pre-check target capacity if moving to a different date/slot
    if (new_date != old_date or new_slot != old_slot):
        if not check_capacity_available(db, new_date, new_slot, group_size):
            from models.booking_model import get_remaining_seats
            remaining = get_remaining_seats(db, new_date, new_slot)
            if group_size < 4:
                error_msg = 'Oops! Your group size is below the minimum requirement. You are not able to proceed further'
            elif remaining is not None and group_size > remaining:
                error_msg = f'Only {remaining} seats left for {new_date} at {new_slot}. You selected {group_size}. Please select another date/slot.'
            else:
                error_msg = f'Timeslot {new_date} at {new_slot} is full. Please select another date/slot.'
            return jsonify({'error': error_msg}), 400

        # Free any provisional or prior raft allocations in old slot
        if booking.get('raft_allocations'):
            deallocate_amount_from_booking(db, booking, group_size)
        if old_date and old_slot:
            recompute_occupancy_for_slot(db, old_date, old_slot)
    else:
        # Same slot rebook retry: clear existing allocation to allow clean reallocation
        if booking.get('raft_allocations'):
            deallocate_amount_from_booking(db, booking, group_size)
            recompute_occupancy_for_slot(db, old_date, old_slot)
        
    alloc_res = allocate_raft(db, None, new_date, new_slot, group_size)
    
    if alloc_res.get("status") != "Confirmed":
        if old_date and old_slot:
            recompute_occupancy_for_slot(db, old_date, old_slot)
        from models.booking_model import get_remaining_seats
        remaining = get_remaining_seats(db, new_date, new_slot)
        if group_size < 4:
            error_msg = 'Oops! Your group size is below the minimum requirement. You are not able to proceed further'
        elif remaining is not None and group_size > remaining:
            error_msg = f'Only {remaining} seats left for {new_date} at {new_slot}. You selected {group_size}. Please select another date/slot.'
        else:
            error_msg = f'Timeslot {new_date} at {new_slot} is full. Please select another date/slot.'
        return jsonify({'error': error_msg}), 400
        
    raft_allocations = alloc_res.get("rafts", []) or []
    raft_details = alloc_res.get("raft_details", []) or []

    # Preserve existing financial amounts if already set on the booking
    existing_details = booking.get('booking_details', {}) or {}
    existing_advance = booking.get('advance_amount')
    if existing_advance is None:
        existing_advance = existing_details.get('advance_amount')
        
    existing_total = booking.get('total_amount') or booking.get('amount')
    if existing_total is None:
        existing_total = existing_details.get('total_amount')

    coupon_code = booking.get('coupon_code') or existing_details.get('coupon_code')
    
    if existing_advance is not None or existing_total is not None:
        final_advance_amount = float(existing_advance if existing_advance is not None else 0.0)
        final_total_amount = float(existing_total if existing_total is not None else 0.0)
        
        discount_applied = booking.get('coupon_discount_applied')
        if discount_applied is None:
            discount_applied = existing_details.get('coupon_discount_applied', 0)
        discount_applied = float(discount_applied or 0)
        
        balance_val = booking.get('balance_amount')
        if balance_val is None:
            balance_val = existing_details.get('balance_amount')
        if balance_val is not None:
            balance_amount = float(balance_val)
        else:
            balance_amount = max(0.0, round(final_total_amount - final_advance_amount, 2))
            
        orig_tot = booking.get('original_total_amount') or existing_details.get('original_total_amount')
        if orig_tot is not None:
            original_total_amount = float(orig_tot)
        else:
            original_total_amount = round(final_total_amount + discount_applied, 2)
            
        orig_adv = booking.get('original_advance_amount') or existing_details.get('original_advance_amount')
        if orig_adv is not None:
            original_advance_amount = float(orig_adv)
        else:
            original_advance_amount = final_advance_amount
            
        rate_val = booking.get('amount_per_person') or existing_details.get('amount_per_person')
        if rate_val is not None:
            amount_per_person = float(rate_val)
        else:
            amount_calc = calculate_total_amount(settings, new_date, group_size)
            amount_per_person = amount_calc['applicable_amount']
            
        price_val = booking.get('price_amount') or existing_details.get('price_amount')
        if price_val is not None:
            price_amount = float(price_val)
        else:
            price_amount = max(0.0, round(amount_per_person * group_size - discount_applied, 2))
            
        fee_val = booking.get('platform_fee')
        if fee_val is None:
            fee_val = existing_details.get('platform_fee')
        if fee_val is not None:
            platform_fee = float(fee_val)
        else:
            platform_fee = max(0.0, round(final_total_amount - price_amount, 2))
    else:
        # Fallback for legacy bookings without financial fields
        amount_calc = calculate_total_amount(settings, new_date, group_size)
        amount_per_person = amount_calc['applicable_amount']
        setting_price_amount = float(amount_per_person * group_size)
        discount_applied = 0
        if coupon_code:
            c_doc = db.coupons.find_one({'code': str(coupon_code).strip().upper()})
            if c_doc:
                dtype = c_doc.get('discount_type', 'per_head')
                dval = float(c_doc.get('discount_value', 0))
                if dtype == 'per_head':
                    discount_applied = round(min(dval, amount_per_person) * group_size, 2)
                elif dtype == 'flat':
                    discount_applied = min(dval, setting_price_amount)
                elif dtype == 'percentage':
                    discount_applied = round((setting_price_amount * dval) / 100.0, 2)

        final_calc = calculate_total_amount(
            settings,
            new_date,
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

    if coupon_code:
        try:
            from models.coupon_model import mark_coupon_used
            mark_coupon_used(db, coupon_code, oid)
        except Exception:
            pass
    
    # Atomic update
    from pymongo import ReturnDocument
    updated = db.bookings.find_one_and_update(
        {'_id': oid},
        {
            "$set": {
                "date": new_date,
                "slot": new_slot,
                "booking_details.date": new_date,
                "booking_details.slot": new_slot,
                "status": "Confirmed",
                "payment_status": "Paid",
                "payment_verified_at": booking.get("payment_verified_at") or datetime.datetime.utcnow(),
                "raft_allocations": raft_allocations,
                "raft_allocation_details": raft_details,
                "allocation_error": False,
                "amount": final_total_amount,
                "total_amount": final_total_amount,
                "original_total_amount": original_total_amount,
                "original_advance_amount": original_advance_amount,
                "amount_per_person": amount_per_person,
                "booking_details.amount_per_person": amount_per_person,
                "booking_details.total_amount": final_total_amount,
                "booking_details.original_total_amount": original_total_amount,
                "booking_details.original_advance_amount": original_advance_amount,
                "advance_amount": final_advance_amount,
                "booking_details.advance_amount": final_advance_amount,
                "balance_amount": balance_amount,
                "booking_details.balance_amount": balance_amount,
                "coupon_discount_applied": discount_applied,
                "booking_details.coupon_discount_applied": discount_applied,
                "coupon_code": coupon_code,
                "booking_details.coupon_code": coupon_code,
                "price_amount": price_amount,
                "booking_details.price_amount": price_amount,
                "platform_fee": platform_fee,
                "booking_details.platform_fee": platform_fee,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    
    # Recompute occupancies to ensure database rafts match confirmed bookings perfectly
    recompute_occupancy_for_slot(db, new_date, new_slot)
    if old_date and old_slot and (old_date != new_date or old_slot != new_slot):
        recompute_occupancy_for_slot(db, old_date, old_slot)

    # Send email
    try:
        from utils.email_service import send_booking_email, send_email_async
        send_email_async(send_booking_email, updated, is_success=True)
        current_app.logger.info(f"[ADMIN-REBOOK] Confirmation email dispatched asynchronously to {updated.get('email')}")
    except Exception as e:
        current_app.logger.error(f"[ADMIN-REBOOK] Error sending email: {e}")
        
    return jsonify({'message': f'Booking rebooked for {new_date} ({new_slot}) and confirmed successfully!'})


@admin_bp.route('/postpone_booking/<booking_id>', methods=['POST'])
@login_required
@admin_required  # Only admin, not subadmin
def postpone_booking_route(booking_id):
    db = current_app.mongo.db
    data = request.get_json() or {}
    new_date = data.get('new_date')
    new_slot = data.get('new_slot')
    if not new_date or not new_slot:
        return jsonify({'error': 'new_date and new_slot required'}), 400
    try:
        oid = ObjectId(booking_id)
    except Exception:
        return jsonify({'error': 'Invalid booking id'}), 400
    
    # Check current status before postponing
    # Pending bookings cannot be postponed (user must confirm/pay first)
    existing_booking = db.bookings.find_one({'_id': oid})
    if existing_booking and existing_booking.get('status') == 'Pending':
        return jsonify({'error': 'Pending bookings cannot be postponed. Please confirm the booking first.'}), 400
    
    # Store old date and slot before postponement
    old_date = existing_booking.get('date') if existing_booking else None
    old_slot = existing_booking.get('slot') if existing_booking else None
    
    print(f"[ADMIN-POSTPONE] Request: booking={booking_id}, new_date={new_date}, new_slot={new_slot}")
    res = postpone_booking(db, oid, new_date, new_slot)
    print(f"[ADMIN-POSTPONE] Result: {res}")
    
    # Return error response with appropriate status code
    if 'error' in res:
        return jsonify(res), 400
    
    # Postponement successful - send email to user
    try:
        updated_booking = db.bookings.find_one({'_id': oid})
        if updated_booking and old_date and old_slot:
            from utils.email_service import send_postponement_email, send_email_async
            send_email_async(send_postponement_email, updated_booking, old_date, old_slot)
            print(f"[ADMIN-POSTPONE] Postponement email dispatched asynchronously to {updated_booking.get('email')}")
    except Exception as e:
        print(f"[ADMIN-POSTPONE] Error sending postponement email: {e}")
        # Don't fail the postponement if email fails, just log the error
    
    return jsonify(res), 200


@admin_bp.route('/manage_users', methods=['GET', 'POST'], endpoint='manage_users')
@admin_bp.route('/manage_subadmins', methods=['GET', 'POST'], endpoint='manage_subadmins')
@admin_bp.route('/manage_admins', methods=['GET', 'POST'], endpoint='manage_admins')
@login_required
@admin_required
def manage_users():
    db = current_app.mongo.db
    is_supreme = current_user.is_supreme_admin()
    
    # Determine default selected role from query param or endpoint
    default_role = request.args.get('role', '')
    if not default_role:
        if request.endpoint == 'admin.manage_admins':
            default_role = 'admin'
        else:
            default_role = 'subadmin'

    if request.method == 'POST':
        action = request.form.get('action', 'create')
        
        # Create user account
        if action == 'create':
            role = request.form.get('role', 'subadmin').strip().lower()
            name = request.form.get('name', '').strip()
            email = request.form.get('email', '').strip().lower()
            phone = request.form.get('phone', '').strip()
            password = request.form.get('password', '').strip()
            confirm_password = request.form.get('confirm_password', '').strip()

            if not email or not password:
                flash('Email and password are required', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            if '@' not in email:
                flash('Invalid email format', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            if len(password) < 8:
                flash('Password must be at least 8 characters long', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            if confirm_password and password != confirm_password:
                flash('Passwords do not match', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            # Permission check: Only Supreme Admin can create 'admin' accounts
            if role == 'admin' and not is_supreme:
                flash('Only Supreme Admin can create Admin accounts', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            if role not in ['admin', 'subadmin']:
                flash('Invalid account role selected', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            # Check if user already exists
            existing = db.users.find_one({'email': email})
            if existing:
                flash('A user with this email already exists', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            try:
                from werkzeug.security import generate_password_hash
                hashed_pw = generate_password_hash(password)
                
                user_name = name if name else email.split('@')[0].title()
                user_phone = phone if phone else '0000000000'
                
                db.users.insert_one({
                    'name': user_name,
                    'email': email,
                    'phone': user_phone,
                    'role': role,
                    'password_hash': hashed_pw
                })
                role_label = 'Admin' if role == 'admin' else 'Subadmin'
                flash(f'✅ {role_label} created successfully: {email}', 'success')
            except Exception as e:
                flash(f'Error creating user: {str(e)}', 'error')

        # Update user role
        elif action in ['update_role', 'change_role']:
            if not is_supreme:
                flash('Only Supreme Admin can change user roles', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            user_id = (request.form.get('user_id') or request.form.get('subadmin_id') or request.form.get('admin_id') or '').strip()
            new_role = (request.form.get('new_role') or request.form.get('role') or '').strip().lower()

            if not user_id:
                flash('Invalid user ID', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            if new_role not in ['admin', 'subadmin']:
                flash('Invalid role selected. Role can only be Admin or Subadmin.', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            try:
                oid = ObjectId(user_id)
                user = db.users.find_one({'_id': oid})

                if not user:
                    flash('User account not found', 'error')
                    return redirect(request.referrer or url_for('admin.manage_users'))

                target_role = user.get('role')
                curr_id = str(getattr(current_user, 'id', '') or getattr(current_user, '_id', ''))
                target_id = str(user.get('_id', ''))
                curr_email = getattr(current_user, 'email', '')
                target_email = user.get('email', '')

                # Self-modification and supreme admin protection
                if target_role == 'supreme_admin' or user.get('is_supreme'):
                    flash('Supreme Admin account role cannot be modified', 'error')
                    return redirect(request.referrer or url_for('admin.manage_users'))

                if (target_id and curr_id and target_id == curr_id) or (target_email and curr_email and target_email == curr_email):
                    flash('You cannot change your own role!', 'error')
                    return redirect(request.referrer or url_for('admin.manage_users'))

                if target_role == new_role:
                    flash(f'User already has the role of {new_role.title()}', 'info')
                    return redirect(request.referrer or url_for('admin.manage_users'))

                db.users.update_one({'_id': oid}, {'$set': {'role': new_role}})
                new_role_label = 'Admin' if new_role == 'admin' else 'Subadmin'
                flash(f'✅ Role for {user.get("email")} updated to {new_role_label}', 'success')
            except Exception as e:
                flash(f'Error updating user role: {str(e)}', 'error')

            return redirect(request.referrer or url_for('admin.manage_users'))

        # Delete user account
        elif action == 'delete':
            user_id = (request.form.get('user_id') or request.form.get('subadmin_id') or request.form.get('admin_id') or '').strip()
            
            if not user_id:
                flash('Invalid user ID', 'error')
                return redirect(request.referrer or url_for('admin.manage_users'))

            try:
                oid = ObjectId(user_id)
                user = db.users.find_one({'_id': oid})

                if not user:
                    flash('User account not found', 'error')
                    return redirect(request.referrer or url_for('admin.manage_users'))

                target_role = user.get('role')
                curr_id = str(getattr(current_user, 'id', '') or getattr(current_user, '_id', ''))
                target_id = str(user.get('_id', ''))
                curr_email = getattr(current_user, 'email', '')
                target_email = user.get('email', '')

                # Self-deletion protection
                if (target_id and curr_id and target_id == curr_id) or (target_email and curr_email and target_email == curr_email):
                    flash('You cannot delete your own account!', 'error')
                    return redirect(request.referrer or url_for('admin.manage_users'))

                # Role permission check: Normal admin can only delete subadmin
                if target_role == 'admin' and not is_supreme:
                    flash('Only Supreme Admin can delete Admin accounts', 'error')
                    return redirect(request.referrer or url_for('admin.manage_users'))

                if target_role not in ['admin', 'subadmin']:
                    flash('Can only delete Admin or Subadmin accounts', 'error')
                    return redirect(request.referrer or url_for('admin.manage_users'))

                db.users.delete_one({'_id': oid})
                role_label = 'Admin' if target_role == 'admin' else 'Subadmin'
                flash(f'✅ {role_label} deleted: {user.get("email")}', 'success')
            except Exception as e:
                flash(f'Error deleting user: {str(e)}', 'error')

            return redirect(request.referrer or url_for('admin.manage_users'))

    # GET: Fetch users for directory
    curr_email = getattr(current_user, 'email', '')
    if is_supreme:
        query = {'role': {'$in': ['admin', 'subadmin']}}
    else:
        # Normal admin manages subadmins only
        query = {'role': 'subadmin'}

    users = list(db.users.find(query).sort([('role', 1), ('name', 1)]))
    
    formatted_users = []
    for u in users:
        u['_id'] = str(u['_id'])
        # Mark whether current user can delete this target user
        if u.get('email') == curr_email:
            u['can_delete'] = False
        elif u.get('role') == 'admin' and not is_supreme:
            u['can_delete'] = False
        else:
            u['can_delete'] = True
        formatted_users.append(u)

    subadmins_list = [u for u in formatted_users if u.get('role') == 'subadmin']
    admins_list = [u for u in formatted_users if u.get('role') == 'admin']

    return render_template(
        'manage_users.html',
        users=formatted_users,
        subadmins=subadmins_list,
        admins=admins_list,
        selected_role=default_role,
        is_supreme=is_supreme
    )


@admin_bp.route('/generate_coupon', methods=['POST'])
@login_required
@admin_required
def generate_coupon():
    db = current_app.mongo.db
    try:
        data = request.get_json(silent=True) or request.form
        phone = data.get('phone', '').strip()
        email = data.get('email', '').strip()
        group_size = data.get('group_size')
        discount_type = data.get('discount_type', 'per_head').strip()
        discount_value = data.get('discount_value', 0)
        valid_day_types = data.get('valid_day_types')
        if not valid_day_types and hasattr(request, 'form'):
            valid_day_types = request.form.getlist('valid_day_types')
        if not valid_day_types:
            valid_day_types = ['weekday', 'weekend']
        elif isinstance(valid_day_types, str):
            valid_day_types = [valid_day_types]

        if not phone:
            return jsonify({'status': 'error', 'message': 'Phone number is required'}), 400

        if not email:
            return jsonify({'status': 'error', 'message': 'Customer email is required'}), 400

        if not email.strip().lower().endswith('@gmail.com'):
            return jsonify({'status': 'error', 'message': 'Please enter a valid Gmail address (ending in @gmail.com)'}), 400

        if group_size is None or str(group_size).strip() == '':
            return jsonify({'status': 'error', 'message': 'Group size is required'}), 400

        coupon_doc = create_coupon(
            db,
            phone=phone,
            discount_type=discount_type,
            discount_value=discount_value,
            created_by=getattr(current_user, 'email', 'admin'),
            email=email if email else None,
            group_size=group_size,
            valid_day_types=valid_day_types
        )

        email_sent = False
        if email:
            try:
                threading.Thread(
                    target=send_coupon_email,
                    args=(email, coupon_doc['code'], discount_type, float(discount_value)),
                    daemon=True
                ).start()
                email_sent = True
            except Exception as e:
                print(f"Error starting coupon email thread: {e}")

        dval = coupon_doc.get('discount_value', 0)
        try:
            dval_clean = int(float(dval)) if float(dval).is_integer() else float(dval)
        except (ValueError, TypeError):
            dval_clean = dval

        session['coupon_generation_success'] = {
            'code': coupon_doc['code'],
            'phone': coupon_doc['phone'],
            'group_size': coupon_doc.get('group_size'),
            'discount_type': coupon_doc['discount_type'],
            'discount_value': dval_clean,
            'email': coupon_doc.get('email')
        }

        return jsonify({
            'status': 'success',
            'code': coupon_doc['code'],
            'phone': coupon_doc['phone'],
            'group_size': coupon_doc.get('group_size'),
            'discount_type': coupon_doc['discount_type'],
            'discount_value': coupon_doc['discount_value'],
            'email_sent': email_sent,
            'message': f"Coupon {coupon_doc['code']} generated successfully!"
        })

    except ValueError as ve:
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': f"Failed to generate coupon: {str(e)}"}), 500


@admin_bp.route('/coupons/create', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_create_coupon():
    """
    Dedicated Coupon Generation Form Page.
    """
    if request.method == 'POST':
        response = generate_coupon()
        if request.is_json:
            return response
        
        if isinstance(response, tuple):
            resp_obj, code = response
        else:
            resp_obj, code = response, 200
        
        data = resp_obj.get_json(silent=True) if hasattr(resp_obj, 'get_json') else {}
        if code == 200 and data.get('status') == 'success':
            return redirect(url_for('admin.coupon_management'))
        else:
            flash(data.get('message', 'Failed to generate coupon'), 'error')
            return render_template('admin_coupon_form.html')

    return render_template('admin_coupon_form.html')


@admin_bp.route('/coupons', methods=['GET'])
@login_required
@admin_required
def coupon_management():
    db = current_app.mongo.db
    from models.coupon_model import get_all_coupons, is_coupon_expired

    # Determine current date in application's configured timezone (IST)
    now_ist = datetime.datetime.now(timezone.utc).astimezone(IST)
    today_ist_str = now_ist.date().strftime('%Y-%m-%d')

    # Detect if user explicitly requested all dates or provided date parameters in request.args
    has_date_param = any(param in request.args for param in ('from_date', 'from', 'start_date', 'to_date', 'to', 'end_date', 'date', 'all_dates', 'all'))
    all_dates_requested = (
        request.args.get('all_dates') in ('1', 'true', 'True', 'yes') or 
        request.args.get('all') in ('1', 'true', 'True', 'yes')
    )

    if all_dates_requested:
        filter_from_date = ''
        filter_to_date = ''
    elif has_date_param:
        filter_from_date = (request.args.get('start_date') or request.args.get('from_date') or request.args.get('from') or request.args.get('date') or '').strip()
        filter_to_date = (request.args.get('end_date') or request.args.get('to_date') or request.args.get('to') or request.args.get('date') or '').strip()
    else:
        # Default view: show only coupons generated today in the configured timezone
        filter_from_date = today_ist_str
        filter_to_date = today_ist_str

    query_filter = {}
    start_ist_dt = None
    end_ist_dt = None

    if filter_from_date and filter_to_date:
        try:
            from_d = datetime.datetime.strptime(filter_from_date, '%Y-%m-%d').date()
            to_d = datetime.datetime.strptime(filter_to_date, '%Y-%m-%d').date()
            if from_d > to_d:
                from_d, to_d = to_d, from_d
            start_ist_dt = datetime.datetime.combine(from_d, datetime.time.min).replace(tzinfo=IST)
            end_ist_dt = datetime.datetime.combine(to_d + timedelta(days=1), datetime.time.min).replace(tzinfo=IST)
            
            start_utc = start_ist_dt.astimezone(timezone.utc)
            end_utc = end_ist_dt.astimezone(timezone.utc)
            start_naive = start_utc.replace(tzinfo=None)
            end_naive = end_utc.replace(tzinfo=None)
            start_iso = start_utc.isoformat()
            end_iso = end_utc.isoformat()

            query_filter['$or'] = [
                {'created_at': {'$gte': start_utc, '$lt': end_utc}},
                {'created_at': {'$gte': start_naive, '$lt': end_naive}},
                {'created_at': {'$gte': start_iso, '$lt': end_iso}},
                {'created_at': {'$gte': start_utc.strftime('%Y-%m-%dT%H:%M:%SZ'), '$lt': end_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}}
            ]
        except Exception as e:
            logger.warning(f"Error parsing coupon date filter range {filter_from_date} - {filter_to_date}: {e}")
    elif filter_from_date:
        try:
            from_d = datetime.datetime.strptime(filter_from_date, '%Y-%m-%d').date()
            start_ist_dt = datetime.datetime.combine(from_d, datetime.time.min).replace(tzinfo=IST)
            start_utc = start_ist_dt.astimezone(timezone.utc)
            start_naive = start_utc.replace(tzinfo=None)
            start_iso = start_utc.isoformat()

            query_filter['$or'] = [
                {'created_at': {'$gte': start_utc}},
                {'created_at': {'$gte': start_naive}},
                {'created_at': {'$gte': start_iso}},
                {'created_at': {'$gte': start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}}
            ]
        except Exception as e:
            logger.warning(f"Error parsing coupon from_date filter {filter_from_date}: {e}")
    elif filter_to_date:
        try:
            to_d = datetime.datetime.strptime(filter_to_date, '%Y-%m-%d').date()
            end_ist_dt = datetime.datetime.combine(to_d + timedelta(days=1), datetime.time.min).replace(tzinfo=IST)
            end_utc = end_ist_dt.astimezone(timezone.utc)
            end_naive = end_utc.replace(tzinfo=None)
            end_iso = end_utc.isoformat()

            query_filter['$or'] = [
                {'created_at': {'$lt': end_utc}},
                {'created_at': {'$lt': end_naive}},
                {'created_at': {'$lt': end_iso}},
                {'created_at': {'$lt': end_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}}
            ]
        except Exception as e:
            logger.warning(f"Error parsing coupon to_date filter {filter_to_date}: {e}")

    raw_coupons = get_all_coupons(db, query_filter=query_filter)
    coupons = []
    filter_status = (request.args.get('status') or '').strip()
    filter_search = (request.args.get('search') or '').strip()

    for c in raw_coupons:
        c['_id'] = str(c['_id'])
        c['is_expired'] = is_coupon_expired(c)
        if c.get('booking_id'):
            c['booking_id'] = str(c['booking_id'])

        cat = c.get('created_at')
        if cat:
            c['created_at_ist'] = utc_to_ist(cat, fmt='%d %b %Y, %I:%M %p')
            c['created_date_iso'] = utc_to_ist(cat, fmt='%Y-%m-%d')
        else:
            c['created_at_ist'] = ''
            c['created_date_iso'] = ''

        # Double check date boundary in application timezone (IST)
        if start_ist_dt or end_ist_dt:
            dt_cat = None
            if isinstance(cat, datetime.datetime):
                dt_cat = cat
            elif isinstance(cat, str):
                try:
                    dt_cat = datetime.datetime.fromisoformat(cat.replace('Z', '+00:00'))
                except Exception:
                    pass
            if dt_cat:
                if dt_cat.tzinfo is None:
                    dt_cat = dt_cat.replace(tzinfo=timezone.utc)
                dt_cat_ist = dt_cat.astimezone(IST)
                if start_ist_dt and dt_cat_ist < start_ist_dt:
                    continue
                if end_ist_dt and dt_cat_ist >= end_ist_dt:
                    continue

        # Optional status filtering
        if filter_status and filter_status.lower() != 'all':
            if c.get('is_revoked') or c.get('is_deleted'):
                c_status = 'Revoked'
            elif c.get('is_used'):
                c_status = 'Used'
            elif c.get('is_expired'):
                c_status = 'Expired'
            else:
                c_status = 'Valid'
            if c_status.lower() != filter_status.lower():
                continue

        # Optional search filtering
        if filter_search:
            s_lower = filter_search.lower()
            code_match = s_lower in str(c.get('code', '')).lower()
            phone_match = s_lower in str(c.get('phone', '')).lower()
            email_match = s_lower in str(c.get('email', '')).lower()
            if not (code_match or phone_match or email_match):
                continue

        coupons.append(c)

    coupon_success_data = session.pop('coupon_generation_success', None)
    return render_template(
        'admin_coupons.html',
        coupons=coupons,
        coupon_success_data=coupon_success_data,
        filter_from_date=filter_from_date,
        filter_to_date=filter_to_date,
        filter_status=filter_status or 'all',
        filter_search=filter_search,
        today_ist_str=today_ist_str
    )


@admin_bp.route('/revoke_coupon', methods=['POST'])
@login_required
@admin_required
def revoke_coupon_route():
    db = current_app.mongo.db
    try:
        data = request.get_json(silent=True) or request.form
        coupon_id = data.get('coupon_id')
        if not coupon_id:
            return jsonify({'status': 'error', 'message': 'Coupon ID is required'}), 400

        from models.coupon_model import revoke_coupon
        success, message = revoke_coupon(db, coupon_id)
        if success:
            return jsonify({'status': 'success', 'message': message})
        else:
            return jsonify({'status': 'error', 'message': message}), 400
    except Exception as e:
        logger.error(f"Error revoking coupon: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to revoke coupon: {str(e)}"}), 500


@admin_bp.route('/delete_coupons_by_date_range', methods=['POST'])
@login_required
@admin_required
def delete_coupons_by_date_range_route():
    db = current_app.mongo.db
    try:
        data = request.get_json(silent=True) or request.form
        start_date = (data.get('start_date') or '').strip()
        end_date = (data.get('end_date') or '').strip()
        status_filter = (data.get('status') or 'all').strip()

        from models.coupon_model import delete_coupons_by_date_range
        success, count, message = delete_coupons_by_date_range(db, start_date, end_date, status_filter=status_filter)
        if success:
            return jsonify({'status': 'success', 'deleted_count': count, 'message': message})
        else:
            return jsonify({'status': 'error', 'message': message}), 400
    except Exception as e:
        logger.error(f"Error deleting coupons by date range: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to delete coupons: {str(e)}"}), 500


@admin_bp.route('/delete_coupon', methods=['POST'])
@login_required
@admin_required
def delete_coupon_route():
    db = current_app.mongo.db
    try:
        data = request.get_json(silent=True) or request.form
        coupon_id = data.get('coupon_id')
        from models.coupon_model import delete_single_coupon
        success, message = delete_single_coupon(db, coupon_id)
        if success:
            return jsonify({'status': 'success', 'message': message})
        else:
            return jsonify({'status': 'error', 'message': message}), 400
    except Exception as e:
        logger.error(f"Error deleting coupon: {e}")
        return jsonify({'status': 'error', 'message': f"Failed to delete coupon: {str(e)}"}), 500


def sync_captured_razorpay_payments(db, max_check=50):
    """
    Check unconfirmed / pending bookings with a razorpay_order_id.
    Queries Razorpay API to verify if payment was captured, and if so,
    confirms payment & allocates rafts automatically.
    """
    try:
        from routes.payment import razorpay_client, confirm_booking_payment
        unconfirmed = list(db.bookings.find({
            "payment_status": {"$ne": "Paid"},
            "razorpay_order_id": {"$exists": True, "$ne": None, "$ne": ""}
        }).sort("created_at", -1).limit(max_check))

        synced_count = 0
        for b in unconfirmed:
            order_id = b.get("razorpay_order_id")
            if not order_id:
                continue
            try:
                order_data = {}
                try:
                    order_data = razorpay_client.order.fetch(order_id) or {}
                except Exception:
                    pass

                payments_res = razorpay_client.order.payments(order_id)
                items = payments_res.get("items", []) if isinstance(payments_res, dict) else []

                captured_payment = next((p for p in items if p.get("status") in ["captured", "authorized"]), None)
                if not captured_payment and order_data.get("status") == "paid":
                    captured_payment = items[0] if items else {"id": b.get("razorpay_payment_id") or f"pay_order_{order_id}"}

                if captured_payment:
                    payment_id = captured_payment.get("id") or b.get("razorpay_payment_id") or f"pay_order_{order_id}"
                    logger.info("Auto-sync: Found captured payment %s for booking %s", payment_id, str(b["_id"]))
                    confirm_booking_payment(db, b, payment_id, order_id=order_id, raw_response=captured_payment)
                    synced_count += 1
            except Exception as pe:
                logger.warning("Auto-sync error checking order %s: %s", order_id, pe)

        return synced_count
    except Exception as e:
        logger.error("Auto-sync error: %s", e)
        return 0


@admin_bp.route('/sync_payments', methods=['POST', 'GET'])
@login_required
@admin_required
def sync_payments_route():
    db = current_app.mongo.db
    synced = sync_captured_razorpay_payments(db, max_check=50)
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "synced_count": synced, "message": f"Successfully synced {synced} captured payment(s)."})
    if synced > 0:
        flash(f"Successfully synced {synced} captured payment(s) from Razorpay!", "info")
    else:
        flash("All payments are already in sync with Razorpay.", "info")
    return redirect(request.referrer or url_for("admin.dashboard"))


@admin_bp.route('/booking', methods=['GET'])
@login_required
@admin_required
def admin_booking():
    """
    Admin Booking History Page.
    Displays customer booking history and filter options.
    """
    db = current_app.mongo.db
    settings = load_settings(db)
    
    today = datetime.date.today()
    start_date_str = settings.get('start_date')
    end_date_str = settings.get('end_date')
    if start_date_str and end_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            start_date = today
            end_date = today + datetime.timedelta(days=settings.get('days', 30))
    else:
        start_date = today
        end_date = today + datetime.timedelta(days=settings.get('days', 30))

    min_date = max(start_date, today)
    max_date = end_date

    # Determine current date in application's configured timezone (IST)
    now_ist = datetime.datetime.now(timezone.utc).astimezone(IST)
    today_ist_str = now_ist.date().strftime('%Y-%m-%d')

    # Detect if user explicitly provided date parameters in request.args
    has_date_param = any(param in request.args for param in ('from_date', 'from', 'to_date', 'to'))
    if has_date_param:
        filter_from_date = (request.args.get('from_date') or request.args.get('from') or '').strip()
        filter_to_date = (request.args.get('to_date') or request.args.get('to') or '').strip()
    else:
        # By default when opening or reloading the Admin Booking History page,
        # show only the bookings created today in the configured timezone.
        filter_from_date = today_ist_str
        filter_to_date = today_ist_str

    filter_slot = (request.args.get('slot') or request.args.get('filter_slot') or '').strip()
    filter_payment_mode = (request.args.get('payment_mode') or request.args.get('payment_method') or request.args.get('filter_payment_mode') or '').strip()

    def get_filtered_admin_bookings():
        query_filter = {'is_admin_booking': True}

        # Date boundary filtering using booking creation timestamp (created_at) in configured timezone (IST)
        if filter_from_date and filter_to_date:
            try:
                from_d = datetime.datetime.strptime(filter_from_date, '%Y-%m-%d').date()
                to_d = datetime.datetime.strptime(filter_to_date, '%Y-%m-%d').date()
                start_ist = datetime.datetime.combine(from_d, datetime.time.min).replace(tzinfo=IST)
                end_ist = datetime.datetime.combine(to_d + datetime.timedelta(days=1), datetime.time.min).replace(tzinfo=IST)
                start_utc = start_ist.astimezone(timezone.utc)
                end_utc = end_ist.astimezone(timezone.utc)
                start_naive = start_utc.replace(tzinfo=None)
                end_naive = end_utc.replace(tzinfo=None)

                query_filter['$or'] = [
                    {'created_at': {'$gte': start_naive, '$lt': end_naive}},
                    {'created_at': {'$gte': start_utc, '$lt': end_utc}},
                    {'created_at': {'$gte': start_utc.isoformat(), '$lt': end_utc.isoformat()}},
                    {
                        '$and': [
                            {'$or': [{'created_at': {'$exists': False}}, {'created_at': None}]},
                            {'date': {'$gte': filter_from_date, '$lte': filter_to_date}}
                        ]
                    }
                ]
            except Exception as e:
                logger.warning(f"Error parsing date filter range {filter_from_date} - {filter_to_date}: {e}")
        elif filter_from_date:
            try:
                from_d = datetime.datetime.strptime(filter_from_date, '%Y-%m-%d').date()
                start_ist = datetime.datetime.combine(from_d, datetime.time.min).replace(tzinfo=IST)
                start_utc = start_ist.astimezone(timezone.utc)
                start_naive = start_utc.replace(tzinfo=None)

                query_filter['$or'] = [
                    {'created_at': {'$gte': start_naive}},
                    {'created_at': {'$gte': start_utc}},
                    {'created_at': {'$gte': start_utc.isoformat()}},
                    {
                        '$and': [
                            {'$or': [{'created_at': {'$exists': False}}, {'created_at': None}]},
                            {'date': {'$gte': filter_from_date}}
                        ]
                    }
                ]
            except Exception as e:
                logger.warning(f"Error parsing filter_from_date {filter_from_date}: {e}")
        elif filter_to_date:
            try:
                to_d = datetime.datetime.strptime(filter_to_date, '%Y-%m-%d').date()
                end_ist = datetime.datetime.combine(to_d + datetime.timedelta(days=1), datetime.time.min).replace(tzinfo=IST)
                end_utc = end_ist.astimezone(timezone.utc)
                end_naive = end_utc.replace(tzinfo=None)

                query_filter['$or'] = [
                    {'created_at': {'$lt': end_naive}},
                    {'created_at': {'$lt': end_utc}},
                    {'created_at': {'$lt': end_utc.isoformat()}},
                    {
                        '$and': [
                            {'$or': [{'created_at': {'$exists': False}}, {'created_at': None}]},
                            {'date': {'$lte': filter_to_date}}
                        ]
                    }
                ]
            except Exception as e:
                logger.warning(f"Error parsing filter_to_date {filter_to_date}: {e}")

        if filter_slot and filter_slot != 'All':
            query_filter['slot'] = filter_slot

        if filter_payment_mode and filter_payment_mode != 'All':
            query_filter['payment_mode'] = filter_payment_mode

        admin_bookings_cursor = list(db.bookings.find(query_filter).sort('created_at', -1))
        bookings_list = []
        for b in admin_bookings_cursor:
            b['_id'] = str(b.get('_id'))
            ca = b.get('created_at')
            b['created_at_ist'] = utc_to_ist(ca)
            # Compute created_date_ist (YYYY-MM-DD) in configured timezone
            ca_date_ist = ""
            if ca:
                if isinstance(ca, str):
                    try:
                        dt = datetime.datetime.fromisoformat(ca.replace('Z', '+00:00'))
                    except Exception:
                        dt = None
                elif isinstance(ca, datetime.datetime):
                    dt = ca
                else:
                    dt = None
                if dt:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    ca_date_ist = dt.astimezone(IST).strftime('%Y-%m-%d')
            b['created_date_ist'] = ca_date_ist or b.get('date', '')
            b['advance_amount'] = float(b.get('advance_amount', 0) or 0)
            b['total_amount'] = float(b.get('amount') or b.get('total_amount', 0) or 0)
            b['balance_amount'] = float(b.get('balance_amount', 0) or 0)
            bookings_list.append(b)

        # Precise post-filter check on creation date in IST
        if filter_from_date or filter_to_date:
            filtered_list = []
            for b in bookings_list:
                c_date = b.get('created_date_ist')
                if not c_date:
                    filtered_list.append(b)
                    continue
                if filter_from_date and c_date < filter_from_date:
                    continue
                if filter_to_date and c_date > filter_to_date:
                    continue
                filtered_list.append(b)
            return filtered_list

        return bookings_list

    admin_bookings = get_filtered_admin_bookings()
    booking_success_data = session.pop('admin_booking_success', None)

    all_slots = get_slots_for_date_range(db, filter_from_date, filter_to_date, settings)
    admin_settings = dict(settings)
    admin_settings['time_slots'] = all_slots

    return render_template(
        'admin_booking.html',
        settings=admin_settings,
        all_slots=all_slots,
        min_date=min_date.isoformat(),
        max_date=max_date.isoformat(),
        admin_bookings=admin_bookings,
        filter_from_date=filter_from_date,
        filter_to_date=filter_to_date,
        filter_slot=filter_slot,
        filter_payment_mode=filter_payment_mode,
        booking_success_data=booking_success_data
    )


@admin_bp.route('/booking/create', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_create_booking():
    """
    Dedicated Admin Booking Form Page.
    """
    db = current_app.mongo.db
    settings = load_settings(db)
    
    today = datetime.date.today()
    start_date_str = settings.get('start_date')
    end_date_str = settings.get('end_date')
    if start_date_str and end_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            start_date = today
            end_date = today + datetime.timedelta(days=settings.get('days', 30))
    else:
        start_date = today
        end_date = today + datetime.timedelta(days=settings.get('days', 30))

    min_date = max(start_date, today)
    max_date = end_date

    form_data = {}

    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        location = (request.form.get('location') or '').strip()
        booking_date_str = (request.form.get('booking_date') or '').strip()
        slot = (request.form.get('slot') or '').strip()
        group_size_raw = request.form.get('group_size')
        custom_advance_raw = request.form.get('custom_advance_amount')
        per_person_discount_raw = request.form.get('per_person_discount')
        payment_mode = (request.form.get('payment_mode') or 'Offline / Cash').strip()
        coupon_code = (request.form.get('coupon_code') or '').strip().upper()
        admin_notes = (request.form.get('admin_notes') or '').strip()

        form_data = {
            'name': name,
            'email': email,
            'phone': phone,
            'location': location,
            'booking_date': booking_date_str,
            'slot': slot,
            'group_size': group_size_raw,
            'custom_advance_amount': custom_advance_raw,
            'per_person_discount': per_person_discount_raw,
            'payment_mode': payment_mode,
            'coupon_code': coupon_code,
            'admin_notes': admin_notes
        }

        if not name or not phone or not booking_date_str or not group_size_raw:
            flash('Please fill in all required fields.', 'error')
            return render_template('admin_booking_form.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), form_data=form_data)

        valid_time_slots = get_slots_for_date(db, booking_date_str, settings) if settings else []
        if not slot or (valid_time_slots and slot not in valid_time_slots) or slot.strip().lower().startswith('select'):
            flash('Please select a valid time slot.', 'error')
            return render_template('admin_booking_form.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), form_data=form_data)

        try:
            group_size = int(group_size_raw)
            if group_size <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            flash('Invalid group size.', 'error')
            return render_template('admin_booking_form.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), form_data=form_data)

        try:
            per_person_discount = max(0.0, float(str(per_person_discount_raw).strip())) if per_person_discount_raw and str(per_person_discount_raw).strip() != '' else 0.0
        except (ValueError, TypeError):
            per_person_discount = 0.0

        try:
            booking_date = datetime.datetime.strptime(booking_date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Invalid booking date format.', 'error')
            return render_template('admin_booking_form.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), form_data=form_data)

        # Capacity Check
        from utils.effective_availability import calculate_effective_available_capacity
        from utils.booking_ops import check_capacity_available

        eff_max = calculate_effective_available_capacity(db, booking_date_str, slot)
        if group_size < 1 or group_size > eff_max:
            if eff_max <= 0:
                flash('Selected slot is fully booked. Please select another slot or date.', 'error')
            else:
                flash(f'Invalid group size. Maximum available for this slot is {eff_max} people.', 'error')
            return render_template('admin_booking_form.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), form_data=form_data)

        has_capacity = check_capacity_available(db, booking_date_str, slot, group_size, allow_empty_for_small_group=False)
        if group_size < 4 and not has_capacity:
            flash('Oops! Your entered group size is below the minimum requirement.', 'error')
            return render_template('admin_booking_form.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), form_data=form_data)

        # Calculate Pricing & Advance
        amount_calc = calculate_total_amount(settings, booking_date_str, group_size)
        standard_amount_per_person = amount_calc['applicable_amount']
        effective_amount_per_person = max(0.0, round(standard_amount_per_person - per_person_discount, 2))
        base_price_amount = float(effective_amount_per_person * group_size)
        advance_percent = amount_calc.get('advance_percent', 0)
        platform_fee_percent = amount_calc.get('platform_fee_percent', 0)

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
                    per_head_discount = min(dval, effective_amount_per_person)
                    discount_applied = round(per_head_discount * group_size, 2)
                elif dtype == 'flat':
                    discount_applied = min(dval, base_price_amount)
                elif dtype == 'percentage':
                    discount_applied = round((base_price_amount * dval) / 100.0, 2)
            else:
                flash(err or 'Invalid coupon code provided.', 'warning')

        final_calc = calculate_total_amount(
            settings,
            booking_date_str,
            group_size,
            amount_per_person=effective_amount_per_person,
            coupon_discount=discount_applied
        )

        subtotal_price_amount = final_calc['price_amount']

        if custom_advance_raw is not None and str(custom_advance_raw).strip() != '':
            try:
                parsed_adv = max(0.0, round(float(str(custom_advance_raw).strip()), 2))
                final_advance_amount = min(parsed_adv, subtotal_price_amount)
            except (ValueError, TypeError):
                final_advance_amount = 0.0
        else:
            final_advance_amount = 0.0

        platform_fee = 0.0
        platform_fee_percent = 0.0
        final_total_amount = round(subtotal_price_amount, 2)
        balance_amount = max(0.0, round(final_total_amount - final_advance_amount, 2))

        # Allocate Rafts
        from utils.allocation_logic import allocate_raft
        alloc_res = allocate_raft(db, None, booking_date_str, slot, group_size)
        if alloc_res.get('status') != 'Confirmed':
            flash(f'Raft allocation failed: {alloc_res.get("message", "No capacity available")}', 'error')
            return render_template('admin_booking_form.html', settings=settings, min_date=min_date.isoformat(), max_date=max_date.isoformat(), form_data=form_data)

        raft_allocations = alloc_res.get('rafts', [])
        raft_details = alloc_res.get('raft_details', [])

        booking_details = {
            'name': name,
            'email': email,
            'phone': phone,
            'location': location,
            'date': booking_date_str,
            'slot': slot,
            'group_size': group_size,
            'standard_amount_per_person': standard_amount_per_person,
            'per_person_discount': per_person_discount,
            'amount_per_person': effective_amount_per_person,
            'price_amount': subtotal_price_amount,
            'platform_fee': platform_fee,
            'total_amount': final_total_amount,
            'original_total_amount': final_calc['total_amount'],
            'advance_amount': final_advance_amount,
            'original_advance_amount': final_advance_amount,
            'balance_amount': balance_amount,
            'advance_percent': advance_percent,
            'platform_fee_percent': platform_fee_percent,
            'raft_allocations': raft_allocations,
            'raft_allocation_details': raft_details,
            'coupon_code': applied_coupon_code,
            'coupon_discount_applied': discount_applied,
            'is_admin_booking': True,
            'payment_mode': payment_mode,
            'admin_notes': admin_notes,
            'created_by': getattr(current_user, 'name', None) or getattr(current_user, 'email', None) or 'Admin'
        }

        booking_id = create_booking(
            db,
            user_id=email or phone,
            booking_details=booking_details,
            amount=final_total_amount,
            currency='INR',
            status='Confirmed',
            payment_status='Paid',
            allow_payment=True,
            razorpay_order_id=f"ADMIN_{int(datetime.datetime.now().timestamp())}"
        )

        update_booking_status(
            db,
            booking_id,
            status='Confirmed',
            payment_status='Paid',
            raft_allocations=raft_allocations,
            extra_updates={
                'raft_allocation_details': raft_details,
                'is_admin_booking': True,
                'payment_mode': payment_mode,
                'admin_notes': admin_notes
            }
        )

        if applied_coupon_code:
            try:
                from models.coupon_model import mark_coupon_used
                mark_coupon_used(db, applied_coupon_code, str(booking_id))
            except Exception as e:
                logger.error(f"Failed to mark coupon {applied_coupon_code} as used: {e}")

        try:
            from models.payment_model import insert_payment
            insert_payment(
                db,
                booking_id=str(booking_id),
                order_id=f"ADMIN_ORDER_{booking_id}",
                payment_id=f"ADMIN_PAY_{booking_id}",
                signature="OFFLINE_ADMIN_BOOKING",
                amount=final_advance_amount,
                status="paid",
                raw_response={"mode": payment_mode, "created_by_admin": True, "created_by": getattr(current_user, 'name', 'Admin')}
            )
        except Exception as e:
            logger.error(f"Failed to insert admin payment record: {e}")

        if email:
            try:
                booking_doc = db.bookings.find_one({'_id': ObjectId(booking_id)})
                if booking_doc:
                    from utils.email_service import send_booking_email, send_email_async
                    send_email_async(send_booking_email, booking_doc, is_success=True)
            except Exception as e:
                logger.error(f"Failed to send admin booking confirmation email: {e}")

        session['admin_booking_success'] = {
            'booking_id': str(booking_id),
            'name': name,
            'date': booking_date_str,
            'slot': slot,
            'group_size': group_size
        }
        return redirect(url_for('admin.admin_booking'))

    return render_template(
        'admin_booking_form.html',
        settings=settings,
        min_date=min_date.isoformat(),
        max_date=max_date.isoformat(),
        form_data=form_data
    )


@admin_bp.route('/booking/success/<booking_id>', methods=['GET'])
@login_required
@admin_required
def admin_booking_success(booking_id):
    """
    Dedicated Admin Booking Success Page.
    """
    db = current_app.mongo.db
    try:
        booking_doc = db.bookings.find_one({'_id': ObjectId(booking_id)})
    except Exception:
        booking_doc = None

    if not booking_doc:
        flash('Booking not found or invalid booking ID.', 'error')
        return redirect(url_for('admin.admin_booking'))

    booking_doc['_id'] = str(booking_doc.get('_id'))
    booking_doc['created_at_ist'] = utc_to_ist(booking_doc.get('created_at'))
    
    advance_amount = float(booking_doc.get('advance_amount', 0) or 0)
    total_amount = float(booking_doc.get('amount') or booking_doc.get('total_amount', 0) or 0)
    balance_amount = float(booking_doc.get('balance_amount', max(0.0, total_amount - advance_amount)) or 0)
    price_amount = float(booking_doc.get('price_amount') or booking_doc.get('booking_details', {}).get('price_amount') or total_amount)
    
    booking_summary = {
        'id': str(booking_doc.get('_id')),
        'name': booking_doc.get('user_name') or booking_doc.get('name') or booking_doc.get('booking_details', {}).get('name', ''),
        'email': booking_doc.get('email') or booking_doc.get('booking_details', {}).get('email', ''),
        'phone': booking_doc.get('phone') or booking_doc.get('booking_details', {}).get('phone', ''),
        'location': booking_doc.get('location') or booking_doc.get('booking_details', {}).get('location', ''),
        'date': booking_doc.get('date') or booking_doc.get('booking_details', {}).get('date', ''),
        'slot': booking_doc.get('slot') or booking_doc.get('booking_details', {}).get('slot', ''),
        'group_size': booking_doc.get('group_size') or booking_doc.get('booking_details', {}).get('group_size', 0),
        'status': booking_doc.get('status', 'Confirmed'),
        'raft_allocations': booking_doc.get('raft_allocations') or booking_doc.get('booking_details', {}).get('raft_allocations', []),
        'price_amount': price_amount,
        'coupon_code': booking_doc.get('coupon_code') or booking_doc.get('booking_details', {}).get('coupon_code'),
        'coupon_discount_applied': float(booking_doc.get('coupon_discount_applied') or booking_doc.get('booking_details', {}).get('coupon_discount_applied') or 0),
        'per_person_discount': float(booking_doc.get('per_person_discount') or booking_doc.get('booking_details', {}).get('per_person_discount') or 0),
        'total_amount': total_amount,
        'advance_amount': advance_amount,
        'balance_amount': balance_amount,
        'payment_mode': booking_doc.get('payment_mode') or booking_doc.get('booking_details', {}).get('payment_mode', 'Offline / Cash'),
        'admin_notes': booking_doc.get('admin_notes') or booking_doc.get('booking_details', {}).get('admin_notes', ''),
        'created_by': booking_doc.get('created_by') or booking_doc.get('booking_details', {}).get('created_by', 'Admin'),
        'created_at_ist': booking_doc.get('created_at_ist', '')
    }

    return render_template(
        'admin_booking_success.html',
        booking=booking_doc,
        booking_summary=booking_summary
    )


@admin_bp.route('/calculate_admin_booking_pricing', methods=['POST'])
@login_required
@admin_required
def calculate_admin_booking_pricing():
    """
    Calculate live pricing, advance, and capacity breakdown for Admin Booking UI.
    """
    db = current_app.mongo.db
    settings = load_settings(db)
    data = request.get_json(silent=True) or request.form

    booking_date_raw = (data.get('date') or '').strip()
    booking_date_str = booking_date_raw if booking_date_raw else datetime.date.today().isoformat()

    try:
        group_size = int(data.get('group_size', 1))
        if group_size <= 0:
            group_size = 1
    except (ValueError, TypeError):
        group_size = 1

    custom_advance = data.get('custom_advance')
    per_person_discount_raw = data.get('per_person_discount')
    coupon_code = (data.get('coupon_code') or '').strip().upper()
    phone = (data.get('phone') or '').strip()
    email = (data.get('email') or '').strip()
    slot = (data.get('slot') or '').strip()

    try:
        per_person_discount = max(0.0, float(str(per_person_discount_raw).strip())) if per_person_discount_raw and str(per_person_discount_raw).strip() != '' else 0.0
    except (ValueError, TypeError):
        per_person_discount = 0.0

    amount_calc = calculate_total_amount(settings, booking_date_str, group_size)
    standard_amount_per_person = amount_calc['applicable_amount']
    effective_amount_per_person = max(0.0, round(standard_amount_per_person - per_person_discount, 2))
    base_price = float(effective_amount_per_person * group_size)

    discount_applied = 0
    if coupon_code:
        from models.coupon_model import validate_coupon
        coupon_doc, err = validate_coupon(db, phone, coupon_code, email=email, group_size=group_size, booking_date=booking_date_str)
        if coupon_doc and not err:
            dtype = coupon_doc.get('discount_type', 'per_head')
            dval = float(coupon_doc.get('discount_value', 0))
            if dtype == 'per_head':
                discount_applied = round(min(dval, effective_amount_per_person) * group_size, 2)
            elif dtype == 'flat':
                discount_applied = min(dval, base_price)
            elif dtype == 'percentage':
                discount_applied = round((base_price * dval) / 100.0, 2)

    final_calc = calculate_total_amount(
        settings,
        booking_date_str,
        group_size,
        amount_per_person=effective_amount_per_person,
        coupon_discount=discount_applied
    )

    price_amount = final_calc['price_amount']

    if custom_advance is not None and str(custom_advance).strip() != '':
        try:
            parsed_adv = max(0.0, round(float(str(custom_advance).strip()), 2))
            final_advance = min(parsed_adv, price_amount)
        except (ValueError, TypeError):
            final_advance = 0.0
    else:
        final_advance = 0.0

    pf_pct = 0.0
    platform_fee = 0.0
    total_amount = round(price_amount, 2)
    balance_amount = max(0.0, round(total_amount - final_advance, 2))

    valid_time_slots = get_slots_for_date(db, booking_date_raw, settings) if settings else []
    if not slot or (valid_time_slots and slot not in valid_time_slots) or slot.strip().lower().startswith('select'):
        slot = ''

    available_capacity = None
    has_small_group_capacity = True
    if booking_date_raw and slot:
        from utils.effective_availability import calculate_effective_available_capacity
        from utils.booking_ops import check_capacity_available
        available_capacity = calculate_effective_available_capacity(db, booking_date_raw, slot)
        has_small_group_capacity = check_capacity_available(db, booking_date_raw, slot, group_size, allow_empty_for_small_group=False)

    return jsonify({
        'success': True,
        'day_type': amount_calc.get('day_type'),
        'standard_amount_per_person': standard_amount_per_person,
        'per_person_discount': per_person_discount,
        'amount_per_person': effective_amount_per_person,
        'base_price': base_price,
        'coupon_discount': discount_applied,
        'price_amount': price_amount,
        'advance_percent': amount_calc.get('advance_percent', 0),
        'default_advance': 0.0,
        'final_advance': final_advance,
        'platform_fee_percent': pf_pct,
        'platform_fee': platform_fee,
        'total_amount': total_amount,
        'balance_amount': balance_amount,
        'available_capacity': available_capacity,
        'has_small_group_capacity': has_small_group_capacity
    })




