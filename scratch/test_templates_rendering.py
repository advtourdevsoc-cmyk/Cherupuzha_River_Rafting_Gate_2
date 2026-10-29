import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import traceback
from datetime import datetime, date, timezone
from bson import ObjectId
from flask import render_template
from scratch.test_all_routes_and_templates import MockDatabase, MockUser

def test_render_all_templates():
    from app import create_app
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    db_inst = MockDatabase()
    class MongoNS:
        db = db_inst
        client = None
    app.mongo = MongoNS()

    # Define test contexts for templates
    dummy_booking_1 = {
        '_id': '60d5ec49f1b2c81234567890',
        'user_name': 'Test User',
        'name': 'Test User',
        'email': 'test@gmail.com',
        'phone': '9876543210',
        'date': '2026-10-08',
        'slot': '7:00am',
        'group_size': 4,
        'status': 'Confirmed',
        'payment_status': 'Paid',
        'total_amount': 4600.0,
        'advance_amount': 1150.0,
        'balance_amount': 3450.0,
        'created_at': datetime.now(timezone.utc),
        'created_at_ist': '08-10-2026 07:00 AM',
        'payment_status_display': 'Paid',
        'advance_percent': 25.0,
        'platform_fee': 27.6,
        'price_amount': 4600.0,
        'coupon_code': 'RAFT-TEST',
        'coupon_discount_applied': 100.0
    }

    dummy_booking_visited = {
        '_id': '60d5ec49f1b2c81234567891',
        'user_name': 'Visited User',
        'email': 'visited@gmail.com',
        'phone': '9876543211',
        'date': '2026-10-08',
        'slot': '10:00am',
        'group_size': 2,
        'status': 'Visited',
        'payment_status': 'Pending',
        'total_amount': 2300.0,
        'advance_amount': 575.0,
        'balance_amount': 1725.0,
        'created_at': datetime.now(timezone.utc),
        'created_at_ist': '08-10-2026 10:00 AM',
        'payment_status_display': '—',
        'advance_percent': 25.0
    }

    dummy_booking_missing = {
        '_id': '60d5ec49f1b2c81234567892',
        'user_name': None,
        'name': None,
        'email': None,
        'phone': None,
        'date': None,
        'slot': None,
        'group_size': None,
        'status': None,
        'payment_status': None,
        'total_amount': None,
        'advance_amount': None,
        'balance_amount': None,
        'created_at': None,
        'created_at_ist': '',
        'payment_status_display': None,
        'advance_percent': None
    }

    settings = {
        'start_date': '2026-10-01',
        'end_date': '2026-10-31',
        'days': 30,
        'slots': 4,
        'rafts_per_slot': 5,
        'capacity': 6,
        'time_slots': ['7:00am', '10:00am', '1:00pm', '3:30pm'],
        'weekday_amount': 1150,
        'saturday_amount': 1350,
        'weekday_advance_percent': 25.0,
        'saturday_advance_percent': 25.0,
        'platform_fee': 2.4
    }

    templates_to_test = [
        ('home.html', {'settings': settings}),
        ('login.html', {}),
        ('verify_otp.html', {'email': 'test@gmail.com'}),
        ('booking.html', {'settings': settings, 'min_date': '2026-10-08', 'max_date': '2026-10-31', 'start_date': '2026-10-01', 'end_date': '2026-10-31'}),
        ('booking_confirmation.html', {'booking': dummy_booking_1}),
        ('booking_confirmation.html', {'booking': dummy_booking_visited}),
        ('booking_confirmation.html', {'booking': dummy_booking_missing}),
        ('track_booking.html', {}),
        ('track_booking_result.html', {'bookings': [dummy_booking_1, dummy_booking_visited, dummy_booking_missing]}),
        ('payment_processing.html', {'booking_id': '60d5ec49f1b2c81234567890'}),
        ('payment_success.html', {}),
        ('payment_failure.html', {}),
        ('admin_dashboard.html', {
            'bookings': [dummy_booking_1, dummy_booking_visited, dummy_booking_missing],
            'is_subadmin': False,
            'settings': settings,
            'filter_from': '2026-10-08',
            'filter_to': '2026-10-08',
            'filter_slot': '',
            'filter_status': '',
            'today_str': '2026-10-08'
        }),
        ('admin_dashboard.html', {
            'bookings_today': [dummy_booking_1],
            'bookings_tomorrow': [dummy_booking_visited],
            'is_subadmin': True,
            'settings': settings,
            'filter_from': '2026-10-08',
            'filter_to': '2026-10-09',
            'filter_slot': '',
            'filter_status': '',
            'today_str': '2026-10-08'
        }),
        ('admin_calendar.html', {'calendar': {'2026-10-08': [dummy_booking_1, dummy_booking_visited]}, 'settings': settings}),
        ('settings.html', {'settings': settings}),
        ('manage_users.html', {'users': [], 'subadmins': [], 'admins': [], 'selected_role': 'subadmin', 'is_supreme': True}),
        ('manage_admins.html', {'users': [], 'subadmins': [], 'admins': [], 'selected_role': 'admin', 'is_supreme': True}),
        ('manage_subadmins.html', {'users': [], 'subadmins': [], 'admins': [], 'selected_role': 'subadmin', 'is_supreme': True}),
        ('admin_coupons.html', {'coupons': []})
    ]

    with app.test_request_context():
        # Set dummy user on g or current_user
        from flask_login import login_user
        mock_u = MockUser('supreme_admin')
        login_user(mock_u)

        for template_name, context in templates_to_test:
            try:
                rendered = render_template(template_name, **context)
                print(f"[OK] Rendered {template_name} (len={len(rendered)})")
            except Exception as e:
                print(f"[FAIL 500] Error rendering {template_name}: {type(e).__name__}: {e}")
                traceback.print_exc()

if __name__ == '__main__':
    test_render_all_templates()
