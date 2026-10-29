from flask_login import UserMixin
from werkzeug.security import check_password_hash
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import random
import string

class User(UserMixin):
    def __init__(self, data):
        if not data:
            self.id = None
            self._id = None
            self.name = None
            self.email = None
            self.phone = None
            self.role = 'user'
            self.password_hash = None
            self.otp = None
            self.otp_expiry = None
            return
        self._id = data.get('_id')
        self.id = str(self._id) if self._id else None
        self.name = data.get('name')
        self.email = data.get('email')
        self.phone = data.get('phone')
        self.role = data.get('role', 'user')
        self.password_hash = data.get('password_hash')
        self.otp = data.get('otp')
        self.otp_expiry = data.get('otp_expiry')
    
    def is_active(self):
        """Required by Flask-Login. User is active if they have an ID."""
        return self.id is not None

    @staticmethod
    def find_by_id(db, uid):
        try:
            doc = db.users.find_one({'_id': ObjectId(uid)})
        except Exception:
            return None
        return User(doc) if doc else None

    def check_password(self, password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role in ['admin', 'supreme_admin']

    def is_supreme_admin(self):
        return self.role == 'supreme_admin'
    
    def is_subadmin(self):
        return self.role == 'subadmin'
    
    def is_admin_or_subadmin(self):
        return self.role in ['admin', 'subadmin', 'supreme_admin']
    
    def generate_otp(self):
        """Generate a 6-digit OTP and set expiry to 5 minutes"""
        otp = ''.join(random.choices(string.digits, k=6))
        self.otp = otp
        self.otp_expiry = datetime.utcnow() + timedelta(minutes=5)
        return otp
    
    def verify_otp(self, otp_input):
        """Verify the OTP and return True if valid and not expired"""
        if not self.otp or not self.otp_expiry:
            return False
        
        if datetime.utcnow() > self.otp_expiry:
            return False  # OTP expired
        
        return self.otp == otp_input
    
    def clear_otp(self):
        """Clear OTP after successful verification"""
        self.otp = None
        self.otp_expiry = None