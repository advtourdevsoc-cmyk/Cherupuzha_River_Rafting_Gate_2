import os
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

logger = logging.getLogger(__name__)


def send_email_async(fn, *args, **kwargs):
    """
    Executes an email sending function asynchronously in a background daemon thread
    with Flask application context, ensuring HTTP request handlers return immediately.
    """
    try:
        from flask import current_app
        app = current_app._get_current_object()
    except Exception:
        app = None

    def _worker():
        if app:
            with app.app_context():
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Background email send failed in {fn.__name__}: {e}")
        else:
            try:
                fn(*args, **kwargs)
            except Exception as e:
                logger.error(f"Background email send failed in {fn.__name__}: {e}")

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread



def _send_smtp_message(msg, recipient_email, description="email"):
    """
    Robust SMTP email sender with connection timeouts and SSL fallback.
    Tries SMTP_SSL (port 465) first (faster, direct SSL without STARTTLS roundtrips),
    and falls back to STARTTLS (port 587) with a 7s timeout to prevent thread hangs.
    """
    sender_email = os.environ.get("GMAIL_EMAIL")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        logger.error(f"Gmail credentials missing in environment variables. Cannot send {description}.")
        return False

    if not recipient_email:
        logger.warning(f"No recipient email provided for sending {description}.")
        return False

    text = msg.as_string()

    # 1. Try direct SSL on port 465 (fastest, direct encryption)
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=7) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, text)
        logger.info(f"Successfully sent {description} to {recipient_email} via SSL:465")
        return True
    except Exception as e_ssl:
        logger.warning(f"SMTP_SSL:465 failed ({e_ssl}), falling back to STARTTLS:587 for {recipient_email}...")

    # 2. Fallback to STARTTLS on port 587 with strict 7s timeout
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=7) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, text)
        logger.info(f"Successfully sent {description} to {recipient_email} via STARTTLS:587")
        return True
    except Exception as e:
        logger.error(f"Failed to send {description} to {recipient_email} via both SSL and STARTTLS: {e}")
        return False


def send_booking_email(booking, is_success=True):
    sender_email = os.environ.get("GMAIL_EMAIL")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        logger.error("Gmail credentials missing in environment variables. Cannot send email.")
        return False

    recipient_email = booking.get("email") or booking.get("booking_details", {}).get("email")
    if not recipient_email:
        logger.warning(f"No email found for booking {booking.get('_id', 'unknown')}")
        return False

    user_name = booking.get("user_name") or booking.get("booking_details", {}).get("name") or "Valued Customer"
    date = booking.get("date") or "N/A"
    slot = booking.get("slot") or "N/A"
    amount = booking.get("amount") or "N/A"
    order_id = booking.get("razorpay_order_id") or "N/A"
    group_size = booking.get("group_size") or booking.get("booking_details", {}).get("group_size") or "N/A"
    
    formatted_date = date
    try:
        from datetime import datetime
        parsed_date = datetime.strptime(date, '%Y-%m-%d')
        formatted_date = parsed_date.strftime('%d %B %Y')
    except Exception:
        pass

    raft_allocations = booking.get("raft_allocations", [])
    if isinstance(raft_allocations, list) and raft_allocations:
        rafts = " & ".join(str(r) for r in raft_allocations)
    elif raft_allocations:
        rafts = str(raft_allocations)
    else:
        rafts = "To be assigned"

    total_amount = amount if str(amount).isdigit() or isinstance(amount, (int, float)) else 0
    advance_paid = 0
    balance_to_pay = total_amount

    try:
        stored_total = booking.get('amount') or booking.get('total_amount')
        stored_advance = booking.get('advance_amount')
        
        if stored_total is not None and stored_advance is not None:
            total_amount = stored_total
            advance_paid = stored_advance
            balance_to_pay = max(total_amount - advance_paid, 0)
        else:
            from flask import current_app
            if current_app:
                from utils.allocation_logic import load_settings
                from utils.amount_calculator import calculate_total_amount
                db = current_app.mongo.db
                settings = load_settings(db)
                calc = calculate_total_amount(settings, date, int(group_size) if str(group_size).isdigit() else 0)
                if calc and calc.get('total_amount'):
                    total_amount = stored_total if stored_total is not None else calc.get('total_amount')
                    advance_paid = calc.get('advance_amount', 0)
                    balance_to_pay = max(total_amount - advance_paid, 0)
    except Exception as e:
        logger.error(f"Could not recalculate pricing for email: {e}")

    def fmt_amt(val):
        try:
            f_val = float(val)
            # Format with exact amount - preserve decimals
            if f_val == int(f_val):
                return f"{int(f_val):,}"
            else:
                return f"{f_val:,.2f}"
        except:
            return str(val)

    total_amount_str = fmt_amt(total_amount)
    advance_paid_str = fmt_amt(advance_paid)
    balance_to_pay_str = fmt_amt(balance_to_pay)
    
    msg = MIMEMultipart()
    msg['From'] = f"River Rafting <{sender_email}>"
    msg['To'] = recipient_email
    
    if is_success:
        msg['Subject'] = "🎉 Your Booking is Confirmed - Rafting Adventure Awaits!"
        
        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: #f9fafb; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #059669 0%, #10b981 100%); color: white; padding: 30px; border-radius: 8px 8px 0 0; text-align: center; }}
                .content {{ background-color: white; padding: 30px; border-left: 4px solid #10b981; }}
                .section {{ margin-bottom: 25px; }}
                .section-title {{ font-weight: bold; color: #1f2937; margin-bottom: 12px; font-size: 16px; }}
                table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
                td {{ padding: 10px; border-bottom: 1px solid #e5e7eb; }}
                .label {{ font-weight: bold; color: #4b5563; width: 40%; }}
                .value {{ color: #1f2937; }}
                .alert {{ background-color: #ecfdf5; border-left: 4px solid #10b981; padding: 15px; margin: 15px 0; border-radius: 4px; }}
                .alert-title {{ font-weight: bold; color: #065f46; margin-bottom: 8px; }}
                .tips {{ background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; margin: 15px 0; border-radius: 4px; }}
                .tip-item {{ margin: 8px 0; padding-left: 20px; position: relative; }}
                .tip-item:before {{ content: "→"; position: absolute; left: 0; color: #d97706; font-weight: bold; }}
                .contact-section {{ background-color: #f0fdf4; border: 2px solid #10b981; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                .contact-method {{ margin: 15px 0; padding: 12px; background-color: white; border-radius: 6px; border-left: 3px solid #10b981; }}
                .contact-label {{ font-weight: bold; color: #059669; display: block; margin-bottom: 5px; }}
                .contact-value {{ color: #1f2937; }}
                .contact-value a {{ color: #0284c7; text-decoration: none; font-weight: 600; }}
                .contact-value a:hover {{ text-decoration: underline; }}
                .button {{ display: inline-block; background: linear-gradient(135deg, #059669 0%, #10b981 100%); color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; margin: 10px 0; font-weight: bold; }}
                .footer {{ background-color: #f3f4f6; padding: 20px; border-radius: 0 0 8px 8px; text-align: center; font-size: 12px; color: #6b7280; border-top: 1px solid #e5e7eb; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1 style="margin: 0; font-size: 28px;">🎉 Booking Confirmed!</h1>
                    <p style="margin: 10px 0 0 0; opacity: 0.9;">Your Rafting Adventure Awaits - Tejaswini River</p>
                </div>
                
                <div class="content">
                    <p>Hi <strong>{user_name}</strong>,</p>
                    
                    <p>Exciting news! Your rafting booking has been confirmed. Get ready for an unforgettable adventure on the Tejaswini River! 🚣‍♂️</p>
                    
                    <div class="section">
                        <div class="section-title">✅ Your Booking Details:</div>
                        <table>
                            <tr>
                                <td class="label">📅 Date:</td>
                                <td class="value"><strong>{formatted_date}</strong></td>
                            </tr>
                            <tr>
                                <td class="label">🕙 Time Slot:</td>
                                <td class="value"><strong>{slot}</strong></td>
                            </tr>
                            <tr>
                                <td class="label">👥 Group Size:</td>
                                <td class="value">{group_size} persons</td>
                            </tr>
                            <tr>
                                <td class="label">🚣 Raft(s):</td>
                                <td class="value">{rafts}</td>
                            </tr>
                            <tr>
                                <td class="label">💰 Total Amount:</td>
                                <td class="value"><strong>₹{total_amount_str}</strong></td>
                            </tr>
                            <tr>
                                <td class="label">💳 Advance Paid:</td>
                                <td class="value">₹{advance_paid_str}</td>
                            </tr>
                            <tr>
                                <td class="label">💵 Balance Due:</td>
                                <td class="value"><strong>₹{balance_to_pay_str}</strong></td>
                            </tr>
                        </table>
                    </div>
                    
                    <div class="alert">
                        <div class="alert-title">📍 Location & Timing:</div>
                        <p style="margin: 10px 0 0 0;">
                            <strong>📌 Meeting Point:</strong> <a href=" https://maps.app.goo.gl/xKUFUh3sMcowPtMR9?g_st=ic" style="color: #059669; text-decoration: none;">Cherupuzha Rafting Gate 2</a><br>
                            <strong>⏰ Arrival Time:</strong> 30 minutes before slot start time<br>
                            <strong>⏳ Activity Duration:</strong> Approximately 2 hours
                        </p>
                    </div>
                    
                    <div class="tips">
                        <div style="font-weight: bold; color: #b45309; margin-bottom: 12px;">👕 What to Bring & Wear:</div>
                        <div class="tip-item">Wear comfortable, quick-dry clothes (you will get wet!)</div>
                        <div class="tip-item">Bring extra clothes and a towel for after the activity</div>
                        <div class="tip-item">Wear water shoes or sandals that won't slip</div>
                        <div class="tip-item">Apply waterproof sunscreen</div>
                        <div class="tip-item">Leave valuables at home or in a secured bag</div>
                    </div>
                    
                    <div class="tips">
                        <div style="font-weight: bold; color: #b45309; margin-bottom: 12px;">⚠️ Important Safety Guidelines:</div>
                        <div class="tip-item">No alcohol before or during the activity</div>
                        <div class="tip-item">Not recommended for participants with heart conditions, asthma, or epilepsy</div>
                        <div class="tip-item">Minimum group size: 4 participants (for weight balance & safety)</div>
                        <div class="tip-item">Children must be accompanied by an adult</div>
                        <div class="tip-item">Follow all instructions from your safety guides</div>
                    </div>
                    
                    <div style="background-color: #eff6ff; border-left: 4px solid #0284c7; padding: 15px; margin: 15px 0; border-radius: 4px;">
                        <div style="font-weight: bold; color: #0c4a6e; margin-bottom: 8px;">💧 What to Expect:</div>
                        <p style="margin: 0; color: #1e3a8a; font-size: 14px;">This is a rain-dependent activity and water levels may vary. We will notify you one day in advance if there are any changes due to weather or water conditions. Cancellation or postponement is allowed in such cases with a full refund.</p>
                    </div>
                    
                    <div class="contact-section">
                        <div style="font-weight: bold; color: #059669; font-size: 16px; margin-bottom: 15px;">📞 Need Any Help? Contact Us:</div>
                        
                        <div class="contact-method">
                            <span class="contact-label">☎️ Call Us:</span>
                            <div class="contact-value">
                                <a href="tel:+919544112264">+91 9544112264</a>
                            </div>
                            <div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Available: 9 AM - 6 PM IST (Mon-Sun)</div>
                        </div>
                        
                        <div class="contact-method">
                            <span class="contact-label">✉️ Email Us:</span>
                            <div class="contact-value">
                                <a href="mailto:advtourdevsoc@gmail.com">advtourdevsoc@gmail.com</a>
                            </div>
                            <div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Response within 2-4 hours</div>
                        </div>
                    </div>
                    
                    <p style="margin-top: 25px; color: #6b7280; font-size: 14px;">
                        Get ready for an incredible adventure! We can't wait to see you on the river. If you have any questions or special requests, feel free to reach out to our team.
                    </p>
                    
                    <p>Best regards,<br><strong>The Cherupuzha River Rafting Team</strong> 🚣‍♂️<br>Where Adventure Meets Safety</p>
                </div>
                
                <div class="footer">
                    <p style="margin: 0;">🌊 Cherupuzha River Rafting - Your Gateway to Adventure</p>
                    <p style="margin: 5px 0 0 0;">© 2026 All Rights Reserved | Booking Ref: {order_id}</p>
                </div>
            </div>
        </body>
        </html>
        """
    else:
        msg['Subject'] = "Payment Failed - Action Required for Your Booking"
        
        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333; line-height: 1.6; }}
                .container {{ max-width: 600px; margin: 0 auto; background-color: #f9fafb; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #dc2626 0%, #ea580c 100%); color: white; padding: 30px; border-radius: 8px 8px 0 0; text-align: center; }}
                .content {{ background-color: white; padding: 30px; border-left: 4px solid #dc2626; }}
                .section {{ margin-bottom: 25px; }}
                .section-title {{ font-weight: bold; color: #1f2937; margin-bottom: 12px; font-size: 16px; }}
                table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
                td {{ padding: 10px; border-bottom: 1px solid #e5e7eb; }}
                .label {{ font-weight: bold; color: #4b5563; width: 40%; }}
                .value {{ color: #1f2937; }}
                .alert {{ background-color: #fef2f2; border-left: 4px solid #dc2626; padding: 15px; margin: 15px 0; border-radius: 4px; }}
                .alert-title {{ font-weight: bold; color: #991b1b; margin-bottom: 8px; }}
                .solutions {{ background-color: #eff6ff; border-left: 4px solid #0284c7; padding: 15px; margin: 15px 0; border-radius: 4px; }}
                .solution-item {{ margin: 8px 0; padding-left: 20px; position: relative; }}
                .solution-item:before {{ content: "✓"; position: absolute; left: 0; color: #0284c7; font-weight: bold; }}
                .contact-section {{ background-color: #f0fdf4; border: 2px solid #22c55e; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                .contact-method {{ margin: 15px 0; padding: 12px; background-color: white; border-radius: 6px; border-left: 3px solid #22c55e; }}
                .contact-label {{ font-weight: bold; color: #15803d; display: block; margin-bottom: 5px; }}
                .contact-value {{ color: #1f2937; }}
                .contact-value a {{ color: #0284c7; text-decoration: none; font-weight: 600; }}
                .contact-value a:hover {{ text-decoration: underline; }}
                .button {{ display: inline-block; background: linear-gradient(135deg, #dc2626 0%, #ea580c 100%); color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; margin: 10px 0; font-weight: bold; }}
                .footer {{ background-color: #f3f4f6; padding: 20px; border-radius: 0 0 8px 8px; text-align: center; font-size: 12px; color: #6b7280; border-top: 1px solid #e5e7eb; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1 style="margin: 0; font-size: 28px;">🌊 Payment Failed</h1>
                    <p style="margin: 10px 0 0 0; opacity: 0.9;">Cherupuzha River Rafting</p>
                </div>
                
                <div class="content">
                    <p>Hi <strong>{user_name}</strong>,</p>
                    
                    <p>Unfortunately, we couldn't process your payment for your rafting booking. The good news? Your slot hasn't been reserved yet, so you can retry whenever you're ready.</p>
                    
                    <div class="section">
                        <div class="section-title">📋 Your Booking Details:</div>
                        <table>
                            <tr>
                                <td class="label">📅 Date:</td>
                                <td class="value">{formatted_date}</td>
                            </tr>
                            <tr>
                                <td class="label">🕙 Time Slot:</td>
                                <td class="value">{slot}</td>
                            </tr>
                            <tr>
                                <td class="label">👥 Group Size:</td>
                                <td class="value">{group_size} persons</td>
                            </tr>
                            <tr>
                                <td class="label">💰 Amount:</td>
                                <td class="value">₹{total_amount_str}</td>
                            </tr>
                            <tr>
                                <td class="label">📝 Order ID:</td>
                                <td class="value">{order_id}</td>
                            </tr>
                        </table>
                    </div>
                    
                    <div class="alert">
                        <div class="alert-title">❌ Why Did This Happen?</div>
                        <ul style="margin: 0; padding-left: 20px;">
                            <li>Insufficient balance in your bank account</li>
                            <li>Your card was declined by the bank</li>
                            <li>Network connectivity issue during transaction</li>
                            <li>Card blocked or expired</li>
                            <li>Mismatched billing details</li>
                        </ul>
                    </div>
                    
                    <div class="solutions">
                        <div style="font-weight: bold; color: #0284c7; margin-bottom: 12px;">✨ Quick Solutions:</div>
                        <div class="solution-item">Check your bank account balance and retry with the same card</div>
                        <div class="solution-item">Try a different credit/debit card for the payment</div>
                        <div class="solution-item">Contact your bank to verify if the transaction was blocked</div>
                        <div class="solution-item">Ensure your card details match your billing address</div>
                        <div class="solution-item">Check your internet connection and retry</div>
                    </div>
                    
                    <div class="section" style="text-align: center;">
                        <a href="https://cherupuzhariverrafting.in/book" class="button">🔄 Retry Your Booking Now</a>
                    </div>
                    
                    <div class="contact-section">
                        <div style="font-weight: bold; color: #15803d; font-size: 16px; margin-bottom: 15px;">📞 Still Need Help? Contact Our Support Team</div>
                        
                        <div class="contact-method">
                            <span class="contact-label">☎️ Call Us:</span>
                            <div class="contact-value">
                                <a href="tel:+919544112264">+91 9544112264</a>
                            </div>
                            <div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Available: 9 AM - 6 PM IST (Mon-Sun)</div>
                        </div>
                        
                        <div class="contact-method">
                            <span class="contact-label">✉️ Email Us:</span>
                            <div class="contact-value">
                                <a href="mailto:advtourdevsoc@gmail.com">advtourdevsoc@gmail.com</a>
                            </div>
                            <div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Response within 2-4 hours</div>
                        </div>
                    </div>
                    
                    <p style="margin-top: 25px; color: #6b7280; font-size: 14px;">
                        We're here to help make your rafting adventure smooth! Don't hesitate to reach out if you have any questions.
                    </p>
                    
                    <p>Best regards,<br><strong>The Cherupuzha River Rafting Team</strong> 🚣‍♂️</p>
                </div>
                
                <div class="footer">
                    <p style="margin: 0;">🌊 Cherupuzha River Rafting - Your Gateway to Adventure</p>
                    <p style="margin: 5px 0 0 0;">© 2026 All Rights Reserved</p>
                </div>
            </div>
        </body>
        </html>
        """

    msg.attach(MIMEText(html_body, 'html'))

    return _send_smtp_message(msg, recipient_email, description="booking confirmation" if is_success else "payment failed notice")


def send_admin_otp_email(admin_email, otp):
    """Send OTP to admin email for 2-step verification"""
    sender_email = os.environ.get("GMAIL_EMAIL")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        logger.error("Gmail credentials missing in environment variables. Cannot send OTP email.")
        return False

    if not admin_email:
        logger.warning("No admin email provided for OTP sending")
        return False

    msg = MIMEMultipart()
    msg['From'] = f"Admin Verification <{sender_email}>"
    msg['To'] = admin_email
    msg['Subject'] = "🔐 Your Admin Panel Login OTP"

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <div style="background-color: #f8f9fa; padding: 30px; border-radius: 8px; max-width: 600px; margin: auto;">
            <div style="text-align: center; margin-bottom: 25px;">
                <h2 style="color: #0d6efd; margin: 0;">🔐 Admin Panel Verification</h2>
            </div>
            
            <p>Hello Admin,</p>
            
            <p>You have initiated a login request to the Admin Panel. To proceed with your login, please enter the following One-Time Password (OTP):</p>
            
            <div style="background-color: #ffffff; border: 2px dashed #0d6efd; padding: 20px; border-radius: 8px; margin: 25px 0; text-align: center;">
                <p style="margin: 0; font-size: 32px; font-weight: bold; color: #0d6efd; letter-spacing: 5px;">{otp}</p>
                <p style="margin: 10px 0 0 0; color: #666; font-size: 14px;">This OTP will expire in 5 minutes</p>
            </div>
            
            <p style="color: #666; font-size: 14px;"><strong>⚠️ Important:</strong></p>
            <ul style="color: #666; font-size: 14px;">
                <li>Never share this OTP with anyone</li>
                <li>This OTP is valid for only 5 minutes</li>
                <li>If you did not attempt to login, please ignore this email</li>
                <li>Contact support if you did not request this login</li>
            </ul>
            
            <hr style="margin: 25px 0; border: none; border-top: 1px solid #ddd;">
            
            <p style="color: #999; font-size: 12px; margin-top: 20px;">
                This is an automated message from River Rafting Admin System<br>
                © 2025 River Rafting. All rights reserved.
            </p>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, 'html'))

    return _send_smtp_message(msg, admin_email, description="Admin Login OTP")


def send_postponement_email(booking, old_date, old_slot):
    """Send postponement notification email to the booking user"""
    sender_email = os.environ.get("GMAIL_EMAIL")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        logger.error("Gmail credentials missing in environment variables. Cannot send postponement email.")
        return False

    recipient_email = booking.get("email") or booking.get("booking_details", {}).get("email")
    if not recipient_email:
        logger.warning(f"No email found for booking {booking.get('_id', 'unknown')}")
        return False

    user_name = booking.get("user_name") or booking.get("booking_details", {}).get("name") or "Valued Customer"
    new_date = booking.get("date") or "N/A"
    new_slot = booking.get("slot") or "N/A"
    amount = booking.get("amount") or "N/A"
    order_id = booking.get("razorpay_order_id") or "N/A"
    group_size = booking.get("group_size") or booking.get("booking_details", {}).get("group_size") or "N/A"
    
    # Format dates
    formatted_old_date = old_date
    formatted_new_date = new_date
    try:
        from datetime import datetime
        parsed_old_date = datetime.strptime(old_date, '%Y-%m-%d')
        formatted_old_date = parsed_old_date.strftime('%d %B %Y')
        parsed_new_date = datetime.strptime(new_date, '%Y-%m-%d')
        formatted_new_date = parsed_new_date.strftime('%d %B %Y')
    except Exception:
        pass

    raft_allocations = booking.get("raft_allocations", [])
    if isinstance(raft_allocations, list) and raft_allocations:
        rafts = " & ".join(str(r) for r in raft_allocations)
    elif raft_allocations:
        rafts = str(raft_allocations)
    else:
        rafts = "To be assigned"

    total_amount = amount if str(amount).isdigit() or isinstance(amount, (int, float)) else 0
    advance_paid = 0
    balance_to_pay = total_amount

    try:
        stored_total = booking.get('amount') or booking.get('total_amount')
        stored_advance = booking.get('advance_amount')
        
        if stored_total is not None and stored_advance is not None:
            total_amount = stored_total
            advance_paid = stored_advance
            balance_to_pay = max(total_amount - advance_paid, 0)
        else:
            from flask import current_app
            if current_app:
                from utils.allocation_logic import load_settings
                from utils.amount_calculator import calculate_total_amount
                db = current_app.mongo.db
                settings = load_settings(db)
                calc = calculate_total_amount(settings, new_date, int(group_size) if str(group_size).isdigit() else 0)
                if calc and calc.get('total_amount'):
                    total_amount = stored_total if stored_total is not None else calc.get('total_amount')
                    advance_paid = calc.get('advance_amount', 0)
                    balance_to_pay = max(total_amount - advance_paid, 0)
    except Exception as e:
        logger.error(f"Could not recalculate pricing for postponement email: {e}")

    def fmt_amt(val):
        try:
            f_val = float(val)
            if f_val == int(f_val):
                return f"{int(f_val):,}"
            else:
                return f"{f_val:,.2f}"
        except:
            return str(val)

    total_amount_str = fmt_amt(total_amount)
    advance_paid_str = fmt_amt(advance_paid)
    balance_to_pay_str = fmt_amt(balance_to_pay)
    
    msg = MIMEMultipart()
    msg['From'] = f"River Rafting <{sender_email}>"
    msg['To'] = recipient_email
    msg['Subject'] = "📅 Your Booking Has Been Rescheduled - New Date & Time!"

    html_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333; line-height: 1.6; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: #f9fafb; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #0891b2 0%, #0e7490 100%); color: white; padding: 30px; border-radius: 8px 8px 0 0; text-align: center; }}
            .content {{ background-color: white; padding: 30px; border-left: 4px solid #0891b2; }}
            .section {{ margin-bottom: 25px; }}
            .section-title {{ font-weight: bold; color: #1f2937; margin-bottom: 12px; font-size: 16px; }}
            .highlight-box {{ background-color: #ecfdff; border: 2px solid #0891b2; padding: 15px; border-radius: 6px; margin: 15px 0; }}
            .date-comparison {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin: 15px 0; }}
            .date-card {{ background-color: #f0f9ff; border-left: 4px solid #0891b2; padding: 15px; border-radius: 6px; }}
            .date-label {{ font-size: 12px; color: #666; text-transform: uppercase; font-weight: bold; margin-bottom: 8px; }}
            .date-value {{ font-size: 18px; font-weight: bold; color: #0891b2; }}
            .time-value {{ font-size: 16px; color: #1f2937; margin-top: 5px; }}
            table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
            td {{ padding: 10px; border-bottom: 1px solid #e5e7eb; }}
            .label {{ font-weight: bold; color: #4b5563; width: 40%; }}
            .value {{ color: #1f2937; }}
            .alert {{ background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; margin: 15px 0; border-radius: 4px; }}
            .alert-title {{ font-weight: bold; color: #92400e; margin-bottom: 8px; }}
            .tips {{ background-color: #ecfdf5; border-left: 4px solid #10b981; padding: 15px; margin: 15px 0; border-radius: 4px; }}
            .tip-item {{ margin: 8px 0; padding-left: 20px; position: relative; }}
            .tip-item:before {{ content: "→"; position: absolute; left: 0; color: #10b981; font-weight: bold; }}
            .contact-section {{ background-color: #f0fdf4; border: 2px solid #10b981; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .contact-method {{ margin: 15px 0; padding: 12px; background-color: white; border-radius: 6px; border-left: 3px solid #10b981; }}
            .contact-label {{ font-weight: bold; color: #059669; display: block; margin-bottom: 5px; }}
            .contact-value {{ color: #1f2937; }}
            .contact-value a {{ color: #0284c7; text-decoration: none; font-weight: 600; }}
            .contact-value a:hover {{ text-decoration: underline; }}
            .footer {{ background-color: #f3f4f6; padding: 20px; border-radius: 0 0 8px 8px; text-align: center; font-size: 12px; color: #6b7280; border-top: 1px solid #e5e7eb; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0; font-size: 28px;">📅 Booking Rescheduled!</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">Your New Date & Time - Tejaswini River</p>
            </div>
            
            <div class="content">
                <p>Hi <strong>{user_name}</strong>,</p>
                
                <p>Good news! Your rafting booking has been successfully rescheduled. Your new adventure date is confirmed! 🚣‍♂️</p>
                
                <div class="highlight-box">
                    <div style="font-weight: bold; color: #0891b2; margin-bottom: 15px; text-align: center;">Your Booking Has Been Moved</div>
                    <div class="date-comparison">
                        <div class="date-card">
                            <div class="date-label">❌ Old Date & Time</div>
                            <div class="date-value">{formatted_old_date}</div>
                            <div class="time-value">{old_slot}</div>
                        </div>
                        <div class="date-card">
                            <div class="date-label">✅ New Date & Time</div>
                            <div class="date-value">{formatted_new_date}</div>
                            <div class="time-value">{new_slot}</div>
                        </div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="section-title">✅ Updated Booking Details:</div>
                    <table>
                        <tr>
                            <td class="label">📅 New Date:</td>
                            <td class="value"><strong>{formatted_new_date}</strong></td>
                        </tr>
                        <tr>
                            <td class="label">🕙 New Time Slot:</td>
                            <td class="value"><strong>{new_slot}</strong></td>
                        </tr>
                        <tr>
                            <td class="label">👥 Group Size:</td>
                            <td class="value">{group_size} persons</td>
                        </tr>
                        <tr>
                            <td class="label">🚣 Raft(s):</td>
                            <td class="value">{rafts}</td>
                        </tr>
                        <tr>
                            <td class="label">💰 Total Amount:</td>
                            <td class="value"><strong>₹{total_amount_str}</strong></td>
                        </tr>
                        <tr>
                            <td class="label">💳 Amount Already Paid:</td>
                            <td class="value">₹{advance_paid_str}</td>
                        </tr>
                    </table>
                </div>
                
                <div class="alert">
                    <div class="alert-title">⏰ Important: Mark Your Calendar</div>
                    <p style="margin: 10px 0 0 0;">
                        Your new rafting date is <strong>{formatted_new_date}</strong> at <strong>{new_slot}</strong>. 
                        Please arrive <strong>30 minutes before the slot start time</strong> at our meeting point.
                    </p>
                </div>
                
                <div style="background-color: #eff6ff; border-left: 4px solid #0284c7; padding: 15px; margin: 15px 0; border-radius: 4px;">
                    <div style="font-weight: bold; color: #0c4a6e; margin-bottom: 8px;">📍 Meeting Point:</div>
                    <p style="margin: 0; color: #1e3a8a; font-size: 14px;">
                        <a href=" https://maps.app.goo.gl/xKUFUh3sMcowPtMR9?g_st=ic" style="color: #059669; text-decoration: none;">Cherupuzha Rafting Gate 2</a><br>
                        <strong>⏰ Arrive:</strong> 30 minutes before slot start<br>
                        <strong>⏳ Duration:</strong> Approximately 2 hours
                    </p>
                </div>
                
                <div class="contact-section">
                    <div style="font-weight: bold; color: #059669; font-size: 16px; margin-bottom: 15px;">📞 Need Any Help? Contact Us:</div>
                    
                    <div class="contact-method">
                        <span class="contact-label">☎️ Call Us:</span>
                        <div class="contact-value">
                            <a href="tel:+919544112264">+91 9544112264</a>
                        </div>
                        <div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Available: 9 AM - 6 PM IST (Mon-Sun)</div>
                    </div>
                    
                    <div class="contact-method">
                        <span class="contact-label">✉️ Email Us:</span>
                        <div class="contact-value">
                            <a href="mailto:advtourdevsoc@gmail.com">advtourdevsoc@gmail.com</a>
                        </div>
                        <div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Response within 2-4 hours</div>
                    </div>
                </div>
                
                <p style="margin-top: 25px; color: #6b7280; font-size: 14px;">
                    We're looking forward to seeing you at your new scheduled time! If you have any questions about the reschedule or your booking, please don't hesitate to reach out.
                </p>
                
                <p>Best regards,<br><strong>The Cherupuzha River Rafting Team</strong> 🚣‍♂️<br>Where Adventure Meets Safety</p>
            </div>
            
            <div class="footer">
                <p style="margin: 0;">🌊 Cherupuzha River Rafting - Your Gateway to Adventure</p>
                <p style="margin: 5px 0 0 0;">© 2026 All Rights Reserved | Booking Ref: {order_id}</p>
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, 'html'))

    return _send_smtp_message(msg, recipient_email, description="rescheduled booking notification")


def send_cancellation_email(booking, cancellation_reason="Due to unfavourable river conditions, safety considerations, or upon request, the activity may be cancelled."):
    """Send cancellation notification email to the booking user"""
    sender_email = os.environ.get("GMAIL_EMAIL")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        logger.error("Gmail credentials missing in environment variables. Cannot send cancellation email.")
        return False

    recipient_email = booking.get("email") or booking.get("booking_details", {}).get("email")
    if not recipient_email:
        logger.warning(f"No email found for booking {booking.get('_id', 'unknown')}")
        return False

    user_name = booking.get("user_name") or booking.get("booking_details", {}).get("name") or "Valued Guest"
    date = booking.get("date") or "N/A"
    slot = booking.get("slot") or "N/A"
    amount = booking.get("amount") or "N/A"
    order_id = booking.get("razorpay_order_id") or "N/A"
    group_size = booking.get("group_size") or booking.get("booking_details", {}).get("group_size") or "N/A"
    
    # Format date
    formatted_date = date
    try:
        from datetime import datetime
        parsed_date = datetime.strptime(date, '%Y-%m-%d')
        formatted_date = parsed_date.strftime('%d %B %Y')
    except Exception:
        pass

    total_amount = amount if str(amount).isdigit() or isinstance(amount, (int, float)) else 0
    advance_paid = 0

    try:
        stored_total = booking.get('amount') or booking.get('total_amount')
        stored_advance = booking.get('advance_amount')
        
        if stored_total is not None and stored_advance is not None:
            total_amount = stored_total
            advance_paid = stored_advance
        else:
            from flask import current_app
            if current_app:
                from utils.allocation_logic import load_settings
                from utils.amount_calculator import calculate_total_amount
                db = current_app.mongo.db
                settings = load_settings(db)
                calc = calculate_total_amount(settings, date, int(group_size) if str(group_size).isdigit() else 0)
                if calc and calc.get('total_amount'):
                    total_amount = stored_total if stored_total is not None else calc.get('total_amount')
                    advance_paid = calc.get('advance_amount', 0)
    except Exception as e:
        logger.error(f"Could not recalculate pricing for cancellation email: {e}")

    def fmt_amt(val):
        try:
            f_val = float(val)
            if f_val == int(f_val):
                return f"{int(f_val):,}"
            else:
                return f"{f_val:,.2f}"
        except:
            return str(val)

    total_amount_str = fmt_amt(total_amount)
    advance_paid_str = fmt_amt(advance_paid)
    
    msg = MIMEMultipart()
    msg['From'] = f"River Rafting <{sender_email}>"
    msg['To'] = recipient_email
    msg['Subject'] = "🌊 Cherupuzha River Rafting – Cancellation Notice"

    html_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333; line-height: 1.6; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: #f9fafb; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #dc2626 0%, #991b1b 100%); color: white; padding: 30px; border-radius: 8px 8px 0 0; text-align: center; }}
            .content {{ background-color: white; padding: 30px; border-left: 4px solid #dc2626; }}
            .section {{ margin-bottom: 25px; }}
            .section-title {{ font-weight: bold; color: #1f2937; margin-bottom: 12px; font-size: 16px; }}
            .cancellation-notice {{ background-color: #fef2f2; border: 2px solid #dc2626; padding: 20px; border-radius: 6px; margin: 15px 0; }}
            .notice-title {{ font-size: 16px; font-weight: bold; color: #991b1b; margin-bottom: 12px; }}
            .refund-info {{ background-color: #ecfdf5; border-left: 4px solid #10b981; padding: 15px; margin: 15px 0; border-radius: 4px; }}
            .refund-title {{ font-weight: bold; color: #065f46; margin-bottom: 8px; }}
            table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
            td {{ padding: 10px; border-bottom: 1px solid #e5e7eb; }}
            .label {{ font-weight: bold; color: #4b5563; width: 40%; }}
            .value {{ color: #1f2937; }}
            .contact-section {{ background-color: #f0fdf4; border: 2px solid #10b981; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .contact-method {{ margin: 15px 0; padding: 12px; background-color: white; border-radius: 6px; border-left: 3px solid #10b981; }}
            .contact-label {{ font-weight: bold; color: #059669; display: block; margin-bottom: 5px; }}
            .contact-value {{ color: #1f2937; }}
            .contact-value a {{ color: #0284c7; text-decoration: none; font-weight: 600; }}
            .contact-value a:hover {{ text-decoration: underline; }}
            .apology-section {{ background-color: #eff6ff; border-left: 4px solid #0284c7; padding: 15px; margin: 15px 0; border-radius: 4px; }}
            .footer {{ background-color: #f3f4f6; padding: 20px; border-radius: 0 0 8px 8px; text-align: center; font-size: 12px; color: #6b7280; border-top: 1px solid #e5e7eb; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0; font-size: 28px;">🌊 Cancellation Notice</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">Cherupuzha River Rafting</p>
            </div>
            
            <div class="content">
                <p>Dear <strong>{user_name}</strong>,</p>
                
                <div class="cancellation-notice">
                    <div class="notice-title">⚠️ Booking Cancelled</div>
                    <p style="margin: 10px 0 0 0; font-size: 15px;">
                        {cancellation_reason}
                    </p>
                    <p style="margin: 12px 0 0 0; font-size: 16px; font-weight: bold; color: #dc2626;">
                        📅 {formatted_date} – {slot}
                    </p>
                </div>
                
                <div class="section">
                    <div class="section-title">📋 Cancelled Booking Details:</div>
                    <table>
                        <tr>
                            <td class="label">📅 Date:</td>
                            <td class="value">{formatted_date}</td>
                        </tr>
                        <tr>
                            <td class="label">🕙 Time Slot:</td>
                            <td class="value">{slot}</td>
                        </tr>
                        <tr>
                            <td class="label">👥 Group Size:</td>
                            <td class="value">{group_size} persons</td>
                        </tr>
                        <tr>
                            <td class="label">💰 Total Amount:</td>
                            <td class="value">₹{total_amount_str}</td>
                        </tr>
                        <tr>
                            <td class="label">💳 Amount Paid:</td>
                            <td class="value">₹{advance_paid_str}</td>
                        </tr>
                    </table>
                </div>
                
                <div class="refund-info">
                    <div class="refund-title">💳 Refund Status</div>
                    <p style="margin: 10px 0 0 0; color: #065f46; font-size: 14px;">
                        ✅ Your refund has been initiated and will be processed shortly. 
                        The amount will be credited back to your original payment method within 3-5 business days.
                    </p>
                </div>
                
                <div class="apology-section">
                    <p style="margin: 0; color: #0c4a6e; font-size: 15px;">
                        <strong>Sorry for the inconvenience</strong> and thank you for your understanding. We truly value your interest in experiencing the Cherupuzha River rafting adventure. We hope to welcome you in the future when conditions are ideal! 🚣‍♂️
                    </p>
                </div>
                
                <div class="contact-section">
                    <div style="font-weight: bold; color: #059669; font-size: 16px; margin-bottom: 15px;">📞 Questions? We're Here to Help</div>
                    
                    <div class="contact-method">
                        <span class="contact-label">☎️ Call Us:</span>
                        <div class="contact-value">
                            <a href="tel:+919544112264">+91 9544112264</a>
                        </div>
                        <div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Available: 9 AM - 6 PM IST (Mon-Sun)</div>
                    </div>
                    
                    <div class="contact-method">
                        <span class="contact-label">✉️ Email Us:</span>
                        <div class="contact-value">
                            <a href="mailto:advtourdevsoc@gmail.com">advtourdevsoc@gmail.com</a>
                        </div>
                        <div style="font-size: 12px; color: #6b7280; margin-top: 5px;">Response within 2-4 hours</div>
                    </div>
                </div>
                
                <p style="margin-top: 25px; color: #6b7280; font-size: 14px;">
                    If you would like to reschedule your booking or need any assistance, please feel free to contact us. We look forward to providing you with an unforgettable rafting experience.
                </p>
                
                <p>Best regards,<br><strong>The Cherupuzha River Rafting Team</strong> 🌊<br>Safety & Adventure First</p>
            </div>
            
            <div class="footer">
                <p style="margin: 0;">🌊 Cherupuzha River Rafting - Your Gateway to Adventure</p>
                <p style="margin: 5px 0 0 0;">© 2026 All Rights Reserved</p>
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, 'html'))

    return _send_smtp_message(msg, recipient_email, description="cancellation notice")


def send_coupon_email(recipient_email, coupon_code, discount_type, discount_value):
    """Send coupon code email to customer."""
    sender_email = os.environ.get("GMAIL_EMAIL")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        logger.error("Gmail credentials missing in environment variables. Cannot send coupon email.")
        return False

    if not recipient_email:
        logger.warning("No recipient email provided for sending coupon.")
        return False

    if discount_type == "per_head":
        discount_text = f"₹{discount_value:g} OFF per person"
    elif discount_type == "flat":
        discount_text = f"₹{discount_value:g} OFF"
    else:
        discount_text = f"{discount_value:g}% OFF"

    subject = f"Exclusive Rafting Discount Coupon: {coupon_code} - Cherupuzha River Rafting"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; color: #333; }}
            .container {{ max-width: 550px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.08); border-top: 6px solid #0d9488; }}
            .header {{ background: linear-gradient(135deg, #0d9488, #0f766e); color: white; padding: 25px; text-align: center; }}
            .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; }}
            .content {{ padding: 30px 25px; }}
            .code-card {{ background-color: #f0fdf4; border: 2px dashed #16a34a; border-radius: 10px; text-align: center; padding: 20px; margin: 25px 0; }}
            .code-title {{ font-size: 13px; font-weight: 700; color: #16a34a; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; }}
            .code {{ font-size: 32px; font-weight: 800; color: #15803d; letter-spacing: 3px; font-family: monospace; margin: 10px 0; }}
            .discount-badge {{ display: inline-block; background-color: #dcfce7; color: #15803d; font-weight: 700; padding: 6px 14px; border-radius: 20px; font-size: 15px; }}
            .steps {{ background: #f8fafc; border-radius: 8px; padding: 18px 20px; margin-top: 20px; border-left: 4px solid #0d9488; }}
            .steps ol {{ margin: 5px 0 0 0; padding-left: 20px; }}
            .steps li {{ margin-bottom: 8px; font-size: 14px; color: #475569; }}
            .btn-container {{ text-align: center; margin: 25px 0 15px 0; }}
            .btn {{ display: inline-block; background: linear-gradient(135deg, #0d9488 0%, #0f766e 100%); color: #ffffff !important; padding: 14px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 16px; box-shadow: 0 4px 10px rgba(13, 148, 136, 0.3); }}
            .footer {{ background-color: #f8fafc; padding: 15px; text-align: center; font-size: 12px; color: #94a3b8; border-top: 1px solid #e2e8f0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🎉 Exclusive Discount Coupon</h1>
            </div>
            <div class="content">
                <p>Hello,</p>
                <p>You have received a special discount coupon for your upcoming river rafting adventure with <strong>Cherupuzha River Rafting</strong>!</p>
                
                <div class="code-card">
                    <div class="code-title">Your Coupon Code</div>
                    <div class="code">{coupon_code}</div>
                    <div class="discount-badge">Discount: {discount_text}</div>
                </div>

                <div class="steps">
                    <strong style="color: #0f766e;">How to Redeem:</strong>
                    <ol>
                        <li>Go to our <a href="https://cherupuzhariverrafting.in/book" style="color: #0d9488; font-weight: 600; text-decoration: underline;">booking page</a> or click the button below.</li>
                        <li>Enter your registered email ID, phone number, and group size.</li>
                        <li>The <strong>Coupon Code</strong> box will automatically appear.</li>
                        <li>The coupon will expire within 12 hours.</li>
                    </ol>
                </div>

                <div class="btn-container">
                    <a href="https://cherupuzhariverrafting.in/book" class="btn">🚣 Book Now & Redeem Coupon</a>
                </div>

                <p style="margin-top: 25px;">We look forward to giving you an unforgettable rafting experience! 🌊</p>
                <p>Best regards,<br><strong>Cherupuzha River Rafting Team</strong></p>
            </div>
            <div class="footer">
                <p>© 2026 Cherupuzha River Rafting. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Cherupuzha River Rafting <{sender_email}>"
    msg["To"] = recipient_email
    msg.attach(MIMEText(html_content, "html"))

    return _send_smtp_message(msg, recipient_email, description="discount coupon")


def send_group_size_edit_email(booking, old_size):
    """Send group size edit notification email to the booking user"""
    sender_email = os.environ.get("GMAIL_EMAIL")
    sender_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not sender_email or not sender_password:
        logger.error("Gmail credentials missing in environment variables. Cannot send group size edit email.")
        return False

    recipient_email = booking.get("email") or booking.get("booking_details", {}).get("email")
    if not recipient_email:
        logger.warning(f"No email found for booking {booking.get('_id', 'unknown')}")
        return False

    user_name = booking.get("user_name") or booking.get("booking_details", {}).get("name") or "Valued Customer"
    date = booking.get("date") or "N/A"
    slot = booking.get("slot") or "N/A"
    new_size = booking.get("group_size") or booking.get("booking_details", {}).get("group_size") or "N/A"

    formatted_date = date
    try:
        from datetime import datetime
        parsed_date = datetime.strptime(date, '%Y-%m-%d')
        formatted_date = parsed_date.strftime('%d %B %Y')
    except Exception:
        pass

    raft_allocations = booking.get("raft_allocations", [])
    if isinstance(raft_allocations, list) and raft_allocations:
        rafts = " & ".join(str(r) for r in raft_allocations)
    elif raft_allocations:
        rafts = str(raft_allocations)
    else:
        rafts = "To be assigned"

    total_amount = booking.get('amount') or booking.get('total_amount') or 0
    advance_paid = booking.get('advance_amount') or 0
    balance_amount = booking.get('balance_amount')
    if balance_amount is None:
        balance_amount = float(total_amount) - float(advance_paid)
    else:
        balance_amount = float(balance_amount)

    def fmt_amt(val):
        try:
            f_val = float(val)
            if f_val < 0:
                abs_v = abs(f_val)
                if abs_v == int(abs_v):
                    return f"-₹{int(abs_v):,}"
                else:
                    return f"-₹{abs_v:,.2f}"
            if f_val == int(f_val):
                return f"₹{int(f_val):,}"
            else:
                return f"₹{f_val:,.2f}"
        except Exception:
            return f"₹{val}"

    total_amount_str = fmt_amt(total_amount)
    advance_paid_str = fmt_amt(advance_paid)
    balance_str = fmt_amt(balance_amount)

    msg = MIMEMultipart()
    msg['From'] = f"River Rafting <{sender_email}>"
    msg['To'] = recipient_email
    msg['Subject'] = "👥 Booking Group Size Updated - Cherupuzha River Rafting"

    html_body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #333; line-height: 1.6; }}
            .container {{ max-width: 600px; margin: 0 auto; background-color: #f9fafb; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%); color: white; padding: 30px; border-radius: 8px 8px 0 0; text-align: center; }}
            .content {{ background-color: white; padding: 30px; border-left: 4px solid #2563eb; }}
            .section {{ margin-bottom: 25px; }}
            .section-title {{ font-weight: bold; color: #1f2937; margin-bottom: 12px; font-size: 16px; }}
            .highlight-box {{ background-color: #eff6ff; border: 2px solid #2563eb; padding: 15px; border-radius: 6px; margin: 15px 0; }}
            .size-comparison {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin: 15px 0; }}
            .size-card {{ background-color: #f0f9ff; border-left: 4px solid #2563eb; padding: 15px; border-radius: 6px; text-align: center; }}
            .size-label {{ font-size: 12px; color: #666; text-transform: uppercase; font-weight: bold; margin-bottom: 8px; }}
            .size-value {{ font-size: 20px; font-weight: bold; color: #2563eb; }}
            table {{ width: 100%; border-collapse: collapse; margin: 15px 0; }}
            td {{ padding: 10px; border-bottom: 1px solid #e5e7eb; }}
            .label {{ font-weight: bold; color: #4b5563; width: 40%; }}
            .value {{ color: #1f2937; }}
            .contact-section {{ background-color: #f0fdf4; border: 2px solid #10b981; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .contact-method {{ margin: 15px 0; padding: 12px; background-color: white; border-radius: 6px; border-left: 3px solid #10b981; }}
            .contact-label {{ font-weight: bold; color: #059669; display: block; margin-bottom: 5px; }}
            .contact-value {{ color: #1f2937; }}
            .contact-value a {{ color: #0284c7; text-decoration: none; font-weight: 600; }}
            .footer {{ background-color: #f3f4f6; padding: 20px; border-radius: 0 0 8px 8px; text-align: center; font-size: 12px; color: #6b7280; border-top: 1px solid #e5e7eb; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0; font-size: 28px;">👥 Group Size Updated!</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">Cherupuzha River Rafting</p>
            </div>
            
            <div class="content">
                <p>Hi <strong>{user_name}</strong>,</p>
                
                <p>Your booking group size has been successfully updated by our team. Please find your updated booking summary below: 🚣‍♂️</p>
                
                <div class="highlight-box">
                    <div style="font-weight: bold; color: #2563eb; margin-bottom: 15px; text-align: center;">Group Size Update</div>
                    <div class="size-comparison">
                        <div class="size-card">
                            <div class="size-label">Previous Size</div>
                            <div class="size-value">{old_size} Persons</div>
                        </div>
                        <div class="size-card">
                            <div class="size-label">Updated Size</div>
                            <div class="size-value">{new_size} Persons</div>
                        </div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="section-title">✅ Updated Booking Summary:</div>
                    <table>
                        <tr>
                            <td class="label">📅 Date:</td>
                            <td class="value"><strong>{formatted_date}</strong></td>
                        </tr>
                        <tr>
                            <td class="label">🕙 Time Slot:</td>
                            <td class="value"><strong>{slot}</strong></td>
                        </tr>
                        <tr>
                            <td class="label">👥 New Group Size:</td>
                            <td class="value"><strong>{new_size} persons</strong></td>
                        </tr>
                        <tr>
                            <td class="label">🚣 Assigned Raft(s):</td>
                            <td class="value">{rafts}</td>
                        </tr>
                        <tr>
                            <td class="label">💳 Advance Paid (Fixed):</td>
                            <td class="value">{advance_paid_str}</td>
                        </tr>
                        <tr>
                            <td class="label">💰 Balance Amount:</td>
                            <td class="value"><strong>{balance_str}</strong></td>
                        </tr>
                        <tr>
                            <td class="label">🏷️ Total Booking Amount:</td>
                            <td class="value"><strong>{total_amount_str}</strong></td>
                        </tr>
                    </table>
                </div>
                
                <div class="contact-section">
                    <div style="font-weight: bold; color: #059669; font-size: 16px; margin-bottom: 15px;">📞 Questions? Contact Us:</div>
                    
                    <div class="contact-method">
                        <span class="contact-label">☎️ Call Us:</span>
                        <div class="contact-value">
                            <a href="tel:+919544112264">+91 9544112264</a>
                        </div>
                    </div>
                    
                    <div class="contact-method">
                        <span class="contact-label">✉️ Email Us:</span>
                        <div class="contact-value">
                            <a href="mailto:advtourdevsoc@gmail.com">advtourdevsoc@gmail.com</a>
                        </div>
                    </div>
                </div>
                
                <p>Best regards,<br><strong>The Cherupuzha River Rafting Team</strong> 🚣‍♂️</p>
            </div>
            
            <div class="footer">
                <p style="margin: 0;">🌊 Cherupuzha River Rafting - Your Gateway to Adventure</p>
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_body, 'html'))

    return _send_smtp_message(msg, recipient_email, description="group size update notice")

