import os
from types import SimpleNamespace
from flask import Flask, jsonify, current_app
from pymongo import MongoClient
from flask_login import LoginManager
from config import Config

client = None


def create_app():
    """Application factory. Connects to MongoDB using `pymongo` and registers blueprints."""
    global client
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # Session configuration for OTP verification
    app.config['SESSION_PERMANENT'] = False
    app.config['PERMANENT_SESSION_LIFETIME'] = 600  # 10 minutes
    app.config['SESSION_COOKIE_SECURE'] = False  # Set to True in production with HTTPS
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    # Connect to MongoDB with optimized settings for 2GB RAM environment
    mongo_uri = app.config.get('MONGO_URI')
    
    # RAM-optimized connection pool settings (CRITICAL for 2GB)
    mongo_options = {
        'maxPoolSize': int(os.environ.get('MONGO_MAX_POOL_SIZE', 2)),      # Limit connections
        'minPoolSize': int(os.environ.get('MONGO_MIN_POOL_SIZE', 1)),      # Min connections
        'maxIdleTimeMS': 30000,                                             # Close idle after 30s
        'serverSelectionTimeoutMS': 10000,
        'socketTimeoutMS': 10000,
        'connectTimeoutMS': 10000,
        'retryWrites': True,
    }
    
    client = MongoClient(mongo_uri, **mongo_options)
    # Try to get default database from URI, otherwise use raft_booking
    try:
        db = client.get_default_database()
        if db is None:
            db = client['raft_booking']
    except Exception:
        db = client['raft_booking']

    # Attach a small object with .db and .client for compatibility with existing code
    mongo_ns = SimpleNamespace()
    mongo_ns.client = client
    mongo_ns.db = db
    app.mongo = mongo_ns
    app.config['MONGO_CLIENT'] = client

    # Login manager
    login_manager = LoginManager(app)
    login_manager.login_view = 'auth.login'

    # health endpoint
    @app.route('/health')
    def health():
        try:
            # quick server check
            client.admin.command('ping')
            return jsonify({'status': 'ok', 'db': 'connected'})
        except Exception as e:
            return jsonify({'status': 'error', 'db': str(e)})

    # Jinja2 filter for phone number formatting
    @app.template_filter('format_phone')
    def format_phone(phone):
        if not phone:
            return '-'
        # Keep only digits
        digits = ''.join(filter(str.isdigit, str(phone)))
        
        # If 10 digits (common in India), format as +91 XXXXX XXXXX
        if len(digits) == 10:
            return f"+91 {digits[0:5]} {digits[5:10]}"
        elif len(digits) == 12 and digits.startswith('91'):
             return f"+{digits[0:2]} {digits[2:7]} {digits[7:12]}"
        
        # fallback
        return phone

    # Jinja2 filter for IST datetime formatting
    @app.template_filter('format_ist')
    def format_ist(dt, fmt='%d %b %Y, %I:%M %p'):
        if not dt:
            return '-'
        from datetime import timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        if hasattr(dt, 'tzinfo'):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(IST).strftime(fmt)
        return str(dt)

    # user loader
    @login_manager.user_loader
    def load_user(user_id):
        from models.user_model import User
        try:
            user = User.find_by_id(app.mongo.db, user_id)
            if user and user.is_active():
                return user
            return None
        except Exception as e:
            print(f"[ERROR] Error loading user {user_id}: {str(e)}")
            return None

    # Register blueprints here (import inside factory to avoid circular imports)
    from routes.auth_routes import auth_bp
    from routes.booking_routes import booking_bp
    from routes.admin_routes import admin_bp
    from routes.payment import payment_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(booking_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(payment_bp)

    return app


app = create_app()


if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ['true', '1', 't']
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)
