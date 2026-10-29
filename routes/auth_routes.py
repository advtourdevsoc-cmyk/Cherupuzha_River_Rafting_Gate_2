from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session, jsonify
from flask_login import login_user, logout_user, login_required
from models.user_model import User
from utils.email_service import send_admin_otp_email
import threading
import time

auth_bp = Blueprint('auth', __name__)

def _send_otp_background(recipient_email, otp_code, app_logger=None):
    """Background worker to send OTP email without blocking HTTP responses."""
    try:
        send_admin_otp_email(recipient_email, otp_code)
    except Exception as e:
        if app_logger:
            app_logger.error(f"[ERROR] Failed to send OTP email in background to {recipient_email}: {e}")
        else:
            print(f"[ERROR] Failed to send OTP email in background to {recipient_email}: {e}")

@auth_bp.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        
        if not email or not password:
            flash('Email and password are required', 'error')
            return redirect(url_for('auth.login'))
        
        user_doc = current_app.mongo.db.users.find_one({'email': email})
        if not user_doc:
            flash('Invalid credentials', 'error')
            return redirect(url_for('auth.login'))
        
        user = User(user_doc)
        
        # Check password first
        password_valid = user.check_password(password)
        
        # Check role - allow both admin and subadmin
        role_valid = user.is_admin_or_subadmin()
        
        if password_valid and role_valid:
            # Generate OTP
            otp = user.generate_otp()
            
            # Save OTP to database immediately
            current_app.mongo.db.users.update_one(
                {'_id': user._id},
                {'$set': {'otp': otp, 'otp_expiry': user.otp_expiry}}
            )
            
            # Store user info and send timestamp in session for OTP verification
            session['admin_email'] = email
            session['admin_user_id'] = str(user._id)
            session['otp_sent_time'] = time.time()
            session.permanent = True
            
            # Send OTP email asynchronously in a background thread.
            # This completely eliminates the 5-60+ second synchronous SMTP delay,
            # allowing the verify-otp page to load immediately (<100ms)!
            thread = threading.Thread(
                target=_send_otp_background,
                args=(email, otp, current_app.logger),
                daemon=True
            )
            thread.start()
            
            flash(f'OTP sent to {email}. Please check your email.', 'success')
            return redirect(url_for('auth.verify_otp'))
        else:
            # More specific error message for debugging
            if not password_valid:
                flash('Invalid password', 'error')
            elif not role_valid:
                flash(f'Access denied: Invalid role (current role: {user.role})', 'error')
            else:
                flash('Invalid credentials or not authorized', 'error')
            return redirect(url_for('auth.login'))
    
    return render_template('login.html')


@auth_bp.route('/verify-otp', methods=['GET','POST'])
def verify_otp():
    # Check if user has started the login process
    admin_email = session.get('admin_email')
    admin_user_id = session.get('admin_user_id')
    
    if not admin_email or not admin_user_id:
        flash('Please login first', 'error')
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        otp_input = request.form.get('otp', '').strip()
        
        if not otp_input:
            flash('Please enter the OTP', 'error')
            return redirect(url_for('auth.verify_otp'))
        
        # Get user from database
        from bson.objectid import ObjectId
        try:
            user_doc = current_app.mongo.db.users.find_one({'_id': ObjectId(admin_user_id)})
            if not user_doc:
                flash('User not found', 'error')
                session.clear()
                return redirect(url_for('auth.login'))
            
            user = User(user_doc)
            
            # Verify OTP
            if user.verify_otp(otp_input):
                # OTP is valid, clear it from database
                current_app.mongo.db.users.update_one(
                    {'_id': user._id},
                    {'$set': {'otp': None, 'otp_expiry': None}}
                )
                
                # Login the user
                login_user(user, remember=True)
                flash('Logged in successfully', 'success')
                
                # Clear session variables
                session.pop('admin_email', None)
                session.pop('admin_user_id', None)
                session.pop('otp_sent_time', None)
                
                # Redirect to admin dashboard
                next_page = request.args.get('next')
                return redirect(next_page or url_for('admin.dashboard'))
            else:
                flash('Invalid or expired OTP. Please try again.', 'error')
                return redirect(url_for('auth.verify_otp'))
        
        except Exception as e:
            current_app.logger.error(f"[ERROR] OTP verification failed: {str(e)}")
            flash('An error occurred during verification. Please try again.', 'error')
            return redirect(url_for('auth.verify_otp'))
    
    # Calculate cooldown for resend button (30s minimum between resends)
    last_sent = session.get('otp_sent_time', 0)
    remaining_cooldown = max(0, int(30 - (time.time() - last_sent)))
    
    return render_template('verify_otp.html', email=admin_email, remaining_cooldown=remaining_cooldown)


@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    """Resend a new OTP to the admin email with rate limiting (30s cooldown)."""
    admin_email = session.get('admin_email')
    admin_user_id = session.get('admin_user_id')
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json
    
    if not admin_email or not admin_user_id:
        if is_ajax:
            return jsonify({'success': False, 'message': 'Session expired. Please login again.'}), 401
        flash('Please login first', 'error')
        return redirect(url_for('auth.login'))
    
    # 30-second rate limiting cooldown
    last_sent = session.get('otp_sent_time', 0)
    now = time.time()
    cooldown = 30
    if now - last_sent < cooldown:
        remaining = int(cooldown - (now - last_sent))
        msg = f'Please wait {remaining} seconds before requesting a new OTP.'
        if is_ajax:
            return jsonify({'success': False, 'message': msg, 'remaining': remaining}), 429
        flash(msg, 'warning')
        return redirect(url_for('auth.verify_otp'))
    
    from bson.objectid import ObjectId
    try:
        user_doc = current_app.mongo.db.users.find_one({'_id': ObjectId(admin_user_id)})
        if not user_doc:
            session.clear()
            if is_ajax:
                return jsonify({'success': False, 'message': 'User not found. Please login again.'}), 404
            flash('User not found. Please login again.', 'error')
            return redirect(url_for('auth.login'))
        
        user = User(user_doc)
        new_otp = user.generate_otp()
        
        # Save new OTP to database
        current_app.mongo.db.users.update_one(
            {'_id': user._id},
            {'$set': {'otp': new_otp, 'otp_expiry': user.otp_expiry}}
        )
        
        # Update cooldown timer
        session['otp_sent_time'] = now
        
        # Send in background thread
        thread = threading.Thread(
            target=_send_otp_background,
            args=(admin_email, new_otp, current_app.logger),
            daemon=True
        )
        thread.start()
        
        if is_ajax:
            return jsonify({
                'success': True,
                'message': f'New OTP sent to {admin_email}. Please check your email.',
                'cooldown': cooldown
            })
        
        flash(f'New OTP sent to {admin_email}. Please check your email.', 'success')
        return redirect(url_for('auth.verify_otp'))
    except Exception as e:
        current_app.logger.error(f"[ERROR] Resend OTP failed: {str(e)}")
        if is_ajax:
            return jsonify({'success': False, 'message': 'Failed to resend OTP. Please try again.'}), 500
        flash('Failed to resend OTP. Please try again.', 'error')
        return redirect(url_for('auth.verify_otp'))


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('booking.home'))

