"""
Amount calculator utility for booking amounts based on date and group size.
"""
from datetime import datetime


def get_applicable_amount(settings, booking_date_str):
    """
    Determine the applicable amount for a booking based on the day of week.
    
    Args:
        settings (dict): System settings containing weekday_amount and saturday_amount
        booking_date_str (str): Booking date in YYYY-MM-DD format
    
    Returns:
        float: The applicable amount per person (0 if not configured)
    """
    try:
        booking_date = datetime.strptime(booking_date_str, '%Y-%m-%d').date()
        # Monday is 0, Sunday is 6
        weekday = booking_date.weekday()
        
        # Saturday is 5, Sunday is 6
        if weekday == 5 or weekday == 6:
            # Use Saturday amount if available, otherwise fall back to weekday amount
            return float(settings.get('saturday_amount', settings.get('weekday_amount', 0)) or 0)
        else:
            # Monday-Friday (0-4)
            return float(settings.get('weekday_amount', 0) or 0)
    except (ValueError, TypeError):
        return 0.0


def calculate_total_amount(settings, booking_date_str, group_size, amount_per_person=None, coupon_discount=0):
    """
    Calculate the total amount for a booking based on the specified formula:
    
    Calculation breakdown:
        Subtotal (price_amount) = (Amount per person * group_size) - coupon_discount
        Advance Payment = Subtotal * Advance %
        Platform Fee = Advance Payment * Platform Fee %
        Total Amount = Subtotal + Platform Fee
        Balance Amount = Total Amount - Advance Payment
    
    Args:
        settings (dict): System settings containing amount configurations
        booking_date_str (str): Booking date in YYYY-MM-DD format
        group_size (int): Number of people in the group
        amount_per_person (float, optional): Custom per-person rate if overriding setting rate
        coupon_discount (float, optional): Flat or calculated coupon discount amount
    
    Returns:
        dict: Dictionary containing:
            - applicable_amount: Amount per person (float)
            - price_amount: Subtotal after discount for group (float)
            - total_amount: Total cost for the group including platform fee (float)
            - platform_fee: Calculated platform fee amount based on advance payment (float)
            - platform_fee_percent: Configured platform fee percentage (float)
            - advance_percent: Configured advance percentage (float)
            - advance_amount: Calculated advance payment (float)
            - balance_amount: Calculated balance amount to pay on arrival (float)
            - day_type: "weekday" or "weekend"
    """
    try:
        booking_date = datetime.strptime(booking_date_str, '%Y-%m-%d').date()
        weekday = booking_date.weekday()
        
        # Determine day type and applicable amount
        if weekday >= 0 and weekday <= 4:  # Monday to Friday
            default_amount = float(settings.get('weekday_amount', 0) or 0)
            advance_percent = float(settings.get('weekday_advance_percent', 25) or 25)
            day_type = 'weekday'
        else:  # Saturday or Sunday (5 or 6)
            default_amount = float(settings.get('saturday_amount', settings.get('weekday_amount', 0)) or 0)
            advance_percent = float(settings.get('saturday_advance_percent', 35) or 35)
            day_type = 'weekend'
        
        applicable_amount = float(amount_per_person) if amount_per_person is not None else default_amount
        group_size_int = int(group_size) if str(group_size).isdigit() else 0

        # Get platform fee percentage setting (e.g. 2.4 for 2.4%)
        platform_fee_percent = float(settings.get('platform_fee', 0) or 0)
        
        # 1. Base price before discount
        base_price = float(applicable_amount * group_size_int)

        # 2. Subtotal price after coupon discount
        price_amount = max(0.0, round(base_price - float(coupon_discount or 0), 2))

        # 3. Advance Payment = Subtotal * Advance %
        advance_amount = round(price_amount * (advance_percent / 100.0), 2)
        
        # 4. Platform Fee Amount = Advance Payment * (Platform Fee % / 100)
        platform_fee_amount = round(advance_amount * (platform_fee_percent / 100.0), 2)

        # 5. Total Amount = Subtotal + Platform Fee Amount
        total_amount = round(price_amount + platform_fee_amount, 2)

        # 6. Balance Amount = Total Amount - Advance Payment
        balance_amount = max(0.0, round(total_amount - advance_amount, 2))

        return {
            'applicable_amount': applicable_amount,
            'price_amount': price_amount,
            'platform_fee': platform_fee_amount,
            'platform_fee_percent': platform_fee_percent,
            'total_amount': total_amount,
            'advance_percent': advance_percent,
            'advance_amount': advance_amount,
            'balance_amount': balance_amount,
            'day_type': day_type
        }
    except (ValueError, TypeError):
        return {
            'applicable_amount': 0.0,
            'price_amount': 0.0,
            'platform_fee': 0.0,
            'platform_fee_percent': 0.0,
            'total_amount': 0.0,
            'advance_percent': 0.0,
            'advance_amount': 0.0,
            'balance_amount': 0.0,
            'day_type': 'unknown'
        }


def format_currency(amount):
    """Format amount as currency string."""
    return f"₹{amount:.2f}"


def recalculate_booking_resize_amounts(db, booking, new_size, settings=None):
    """
    Recalculate booking pricing, discounts, advance amount, total amount,
    and remaining balance for a booking when its group size is resized.

    Calculation rules:
    - Admin bookings:
        Step 1: Discounted price per person = Standard price per person - Admin-entered discount per person
        Step 2: New booking total = Discounted price per person * Updated group size
        Step 3: Preserve the actual advance amount already recorded for the booking
        Step 4: Remaining balance = New booking total - Advance already paid
        (Zero platform fee, no coupon code required or applied)

    - Customer bookings:
        Preserves customer advance, recalculates coupon discount (if any) for the new size,
        adds platform fee, and sets balance = New total - Advance already paid.
    """
    new_size_int = int(new_size)
    is_admin = bool(booking.get('is_admin_booking') or booking.get('booking_details', {}).get('is_admin_booking', False))
    booking_date = booking.get('date') or booking.get('booking_details', {}).get('date') or ''
    current_size = int(booking.get('group_size', 0) or booking.get('booking_details', {}).get('group_size', 0) or 0)

    if settings is None and db is not None:
        try:
            from utils.allocation_logic import load_settings
            settings = load_settings(db)
        except Exception:
            settings = {}
    elif settings is None:
        settings = {}

    if is_admin:
        # Step 1: Admin-entered per-head discount
        raw_discount = booking.get('per_person_discount')
        if raw_discount is None:
            raw_discount = booking.get('booking_details', {}).get('per_person_discount', 0.0)
        try:
            per_person_discount = max(0.0, float(raw_discount or 0.0))
        except (ValueError, TypeError):
            per_person_discount = 0.0

        # Standard price per person
        raw_std = booking.get('standard_amount_per_person')
        if raw_std is None:
            raw_std = booking.get('booking_details', {}).get('standard_amount_per_person')
        try:
            standard_price = float(raw_std or 0.0)
        except (ValueError, TypeError):
            standard_price = 0.0

        if standard_price <= 0 and settings and booking_date:
            standard_price = float(get_applicable_amount(settings, booking_date) or 0.0)

        if standard_price <= 0:
            stored_per_person = booking.get('amount_per_person') or booking.get('booking_details', {}).get('amount_per_person')
            if stored_per_person is not None:
                try:
                    standard_price = float(stored_per_person) + per_person_discount
                except (ValueError, TypeError):
                    standard_price = 0.0

        if standard_price <= 0 and current_size > 0:
            stored_price = booking.get('price_amount') if booking.get('price_amount') is not None else booking.get('booking_details', {}).get('price_amount')
            if stored_price is not None:
                try:
                    standard_price = (float(stored_price) / current_size) + per_person_discount
                except (ValueError, TypeError):
                    standard_price = 0.0

        standard_price = round(standard_price, 2)

        # Discounted price per person
        discounted_price_per_person = max(0.0, round(standard_price - per_person_discount, 2))

        # Step 2: New booking total
        new_total = round(discounted_price_per_person * new_size_int, 2)
        new_price_amount = new_total
        platform_fee = 0.0
        coupon_discount_applied = 0.0

        # Step 3: Preserve the advance amount
        raw_advance = booking.get('advance_amount') if booking.get('advance_amount') is not None else booking.get('booking_details', {}).get('advance_amount')
        if raw_advance is not None:
            try:
                advance_amount = round(float(raw_advance), 2)
            except (ValueError, TypeError):
                advance_amount = 0.0
        else:
            advance_amount = 0.0

        # Step 4: Calculate the remaining balance
        balance_amount = round(new_total - advance_amount, 2)

        return {
            'is_admin_booking': True,
            'standard_amount_per_person': standard_price,
            'per_person_discount': per_person_discount,
            'amount_per_person': discounted_price_per_person,
            'price_amount': new_price_amount,
            'coupon_discount_applied': coupon_discount_applied,
            'platform_fee': platform_fee,
            'total_amount': new_total,
            'advance_amount': advance_amount,
            'balance_amount': balance_amount,
        }
    else:
        # Customer booking calculation
        amount_per_person = float(booking.get('amount_per_person', 0) or booking.get('booking_details', {}).get('amount_per_person', 0) or 0)
        if amount_per_person == 0 and current_size > 0:
            amount_per_person = float(booking.get('price_amount', 0) or booking.get('booking_details', {}).get('price_amount', 0) or 0) / current_size
        if amount_per_person == 0 and settings and booking_date:
            amount_per_person = float(get_applicable_amount(settings, booking_date) or 0.0)

        old_price_amount = float(booking.get('price_amount') if booking.get('price_amount') is not None else booking.get('booking_details', {}).get('price_amount', current_size * amount_per_person))

        # Preserve existing advance_amount paid by the customer
        existing_advance = booking.get('advance_amount') if booking.get('advance_amount') is not None else booking.get('booking_details', {}).get('advance_amount')
        if existing_advance is not None and float(existing_advance) > 0:
            advance_amount = float(existing_advance)
        else:
            advance_pct = float(booking.get('advance_percent', 25) or 25)
            advance_amount = round(old_price_amount * (advance_pct / 100.0), 2)

        # Recalculate coupon discount for new_size
        coupon_code = booking.get('coupon_code') or booking.get('booking_details', {}).get('coupon_code')
        old_coupon_discount = float(booking.get('coupon_discount_applied', 0) or booking.get('booking_details', {}).get('coupon_discount_applied', 0) or 0)

        new_coupon_discount = 0.0
        if coupon_code and db is not None:
            coupon_doc = db.coupons.find_one({'code': str(coupon_code).strip().upper()})
            if coupon_doc:
                dtype = coupon_doc.get('discount_type', 'per_head')
                dval = float(coupon_doc.get('discount_value', 0))
                if dtype == 'per_head':
                    per_head_disc = min(dval, amount_per_person)
                    new_coupon_discount = round(per_head_disc * new_size_int, 2)
                elif dtype == 'flat':
                    new_coupon_discount = min(dval, new_size_int * amount_per_person)
                elif dtype == 'percentage':
                    new_coupon_discount = round(((new_size_int * amount_per_person) * dval) / 100.0, 2)
            elif old_coupon_discount > 0 and current_size > 0:
                per_head_disc = old_coupon_discount / current_size
                new_coupon_discount = round(per_head_disc * new_size_int, 2)
        elif old_coupon_discount > 0 and current_size > 0:
            per_head_disc = old_coupon_discount / current_size
            new_coupon_discount = round(per_head_disc * new_size_int, 2)

        new_base_price = round(new_size_int * amount_per_person, 2)
        new_price_amount = max(0.0, round(new_base_price - new_coupon_discount, 2))
        platform_fee = float(booking.get('platform_fee') if booking.get('platform_fee') is not None else booking.get('booking_details', {}).get('platform_fee', 0))
        new_total = round(new_price_amount + platform_fee, 2)
        balance_amount = round(new_total - advance_amount, 2)

        return {
            'is_admin_booking': False,
            'standard_amount_per_person': amount_per_person,
            'per_person_discount': 0.0,
            'amount_per_person': amount_per_person,
            'price_amount': new_price_amount,
            'coupon_discount_applied': new_coupon_discount,
            'platform_fee': platform_fee,
            'total_amount': new_total,
            'advance_amount': advance_amount,
            'balance_amount': balance_amount,
        }
