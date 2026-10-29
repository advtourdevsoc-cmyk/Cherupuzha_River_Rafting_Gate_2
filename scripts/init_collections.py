"""
Initialize Database Collections with Proper Schemas and Indexes
This script creates all necessary collections for the Raft Booking System
"""
from pymongo import MongoClient
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import sys
import os
from datetime import datetime, timezone, timedelta

# Load environment variables
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import MONGO_URI as mongo_uri

# Connect to MongoDB
client = MongoClient(mongo_uri)
db = client.get_database("raft_booking")

print("📦 Initializing Database Collections...")
print(f"📍 Database: {db.name}")

# ============================================
# 1. USERS COLLECTION
# ============================================
print("\n🔹 Creating 'users' collection...")
try:
    # Drop if exists
    db.users.drop()
    
    # Create collection with admin user
    supreme_admin_email = os.environ.get("SUPREME_ADMIN_EMAIL", "admin@example.com")
    supreme_admin_password = os.environ.get("SUPREME_ADMIN_PASSWORD", "admin123")
    admin_phone = os.environ.get("ADMIN_PHONE", "9999999999")
    
    hashed_pw = generate_password_hash(supreme_admin_password)
    
    db.users.insert_one({
        "name": "Admin",
        "email": supreme_admin_email,
        "phone": admin_phone,
        "role": "supreme_admin",
        "password_hash": hashed_pw,
        "otp": None,
        "otp_expiry": None,
        "created_at": datetime.now(timezone.utc)
    })
    
    # Create indexes
    db.users.create_index("email", unique=True)
    db.users.create_index("role")
    
    print("   ✅ Users collection created with admin user")
    print(f"      Admin Email: {supreme_admin_email}")
    
except Exception as e:
    print(f"   ❌ Error creating users collection: {e}")

# ============================================
# 2. BOOKINGS COLLECTION
# ============================================
print("\n🔹 Creating 'bookings' collection...")
try:
    db.bookings.drop()
    
    # Create collection with sample structure
    db.bookings.insert_one({
        "_id": None,  # Will be replaced by actual ObjectId
        "user_id": None,
        "booking_details": {
            "date": None,
            "slot": None,
            "user_name": None,
            "name": None,
            "phone": None,
            "email": None,
            "group_size": None,
            "raft_allocations": [],
            "raft_allocation_details": [],
            "advance_amount": 0,
            "advance_percent": 0
        },
        "amount": 0,
        "currency": "INR",
        "status": "Pending",
        "payment_status": "Pending",
        "allow_payment": True,
        "razorpay_order_id": None,
        "razorpay_payment_id": None,
        "platform_fee": 0,
        "price_amount": 0,
        "created_at": datetime.now(timezone.utc)
    })
    
    # Delete the sample document
    db.bookings.delete_one({"_id": db.bookings.find_one({})["_id"]})
    
    # Create indexes
    db.bookings.create_index("user_id")
    db.bookings.create_index("status")
    db.bookings.create_index("payment_status")
    db.bookings.create_index("razorpay_order_id")
    db.bookings.create_index([("booking_details.date", 1), ("booking_details.slot", 1)])
    db.bookings.create_index("created_at")
    
    print("   ✅ Bookings collection created")
    
except Exception as e:
    print(f"   ❌ Error creating bookings collection: {e}")

# ============================================
# 3. PAYMENTS COLLECTION
# ============================================
print("\n🔹 Creating 'payments' collection...")
try:
    db.payments.drop()
    
    # Create collection with sample structure
    db.payments.insert_one({
        "booking_id": None,
        "order_id": None,
        "payment_id": None,
        "signature": None,
        "amount": 0,
        "status": "Pending",
        "created_at": datetime.now(timezone.utc),
        "raw_response": {},
        "webhook_verified": False
    })
    
    # Delete the sample document
    db.payments.delete_one({"booking_id": None})
    
    # Create indexes
    db.payments.create_index("booking_id")
    db.payments.create_index("order_id", unique=True)
    db.payments.create_index("payment_id")
    db.payments.create_index("status")
    db.payments.create_index("created_at")
    
    print("   ✅ Payments collection created")
    
except Exception as e:
    print(f"   ❌ Error creating payments collection: {e}")

# ============================================
# 4. RAFTS COLLECTION
# ============================================
print("\n🔹 Creating 'rafts' collection...")
try:
    db.rafts.drop()
    
    # Create collection with sample structure
    db.rafts.insert_one({
        "day": None,
        "slot": None,
        "raft_id": 0,
        "occupancy": 0,
        "is_special": False,
        "capacity": 0
    })
    
    # Delete the sample document
    db.rafts.delete_one({"day": None})
    
    # Create indexes
    db.rafts.create_index([("day", 1), ("slot", 1), ("raft_id", 1)], unique=True)
    db.rafts.create_index([("day", 1), ("slot", 1)])
    db.rafts.create_index("day")
    
    print("   ✅ Rafts collection created")
    
except Exception as e:
    print(f"   ❌ Error creating rafts collection: {e}")

# ============================================
# 5. SETTINGS COLLECTION
# ============================================
print("\n🔹 Creating 'settings' collection...")
try:
    db.settings.drop()
    
    # Create default system settings
    settings = {
        "_id": "system_settings",
        "days": 30,
        "slots": 4,
        "rafts_per_slot": 5,
        "capacity": 6,
        "time_slots": ["7:00am", "10:00am", "1:00pm", "3:30pm"],
        "platform_fee": 0,
        # Amount configuration (REQUIRED for payments)
        "weekday_amount": 500,  # ₹500 per person for Mon-Fri
        "saturday_amount": 700,  # ₹700 per person for Sat-Sun
        "weekday_advance_percent": 25,  # 25% advance for weekdays
        "saturday_advance_percent": 35,  # 35% advance for weekends
        "updated_at": datetime.now(timezone.utc)
    }
    
    db.settings.insert_one(settings)
    
    print("   ✅ Settings collection created with default values")
    print(f"      Days: {settings['days']}")
    print(f"      Slots: {settings['slots']}")
    print(f"      Rafts per slot: {settings['rafts_per_slot']}")
    print(f"      Capacity per raft: {settings['capacity']}")
    print(f"      Weekday amount: ₹{settings['weekday_amount']} per person")
    print(f"      Saturday amount: ₹{settings['saturday_amount']} per person")
    print(f"      Weekday advance: {settings['weekday_advance_percent']}%")
    print(f"      Weekend advance: {settings['saturday_advance_percent']}%")
    
except Exception as e:
    print(f"   ❌ Error creating settings collection: {e}")

# ============================================
# 6. CREATE SAMPLE RAFTS FOR UPCOMING DATES
# ============================================
print("\n🔹 Creating sample rafts for occupancy overview...")
try:
    # Create rafts for today and next 5 days
    from datetime import date as _date, timedelta
    
    time_slots = ["7:00am", "10:00am", "1:00pm", "3:30pm"]
    rafts_per_slot = 5
    capacity = 6
    
    sample_rafts = []
    current_date = _date.today()
    
    for day_offset in range(6):  # Today + next 5 days
        date_str = (current_date + timedelta(days=day_offset)).isoformat()
        
        for slot in time_slots:
            for raft_id in range(1, rafts_per_slot + 1):
                sample_rafts.append({
                    'day': date_str,
                    'slot': slot,
                    'raft_id': raft_id,
                    'occupancy': 0,
                    'is_special': False,
                    'capacity': capacity
                })
    
    if sample_rafts:
        db.rafts.insert_many(sample_rafts)
        print(f"   ✅ Created {len(sample_rafts)} sample rafts for upcoming 6 days")
    
except Exception as e:
    print(f"   ❌ Error creating sample rafts: {e}")

# ============================================
# 7. DISPLAY FINAL STATE
# ============================================
print("\n" + "="*50)
print("✨ DATABASE INITIALIZATION COMPLETE!")
print("="*50)
print("\n📊 Collections Created:")
for col in db.list_collection_names():
    count = db[col].count_documents({})
    print(f"   • {col:20} ({count} documents)")

print("\n📑 Indexes Created:")
for col in db.list_collection_names():
    indexes = db[col].list_indexes()
    for idx in indexes:
        if idx['name'] != '_id_':
            print(f"   • {col}: {idx['name']}")

print("\n✅ Ready to use! Your database is clean and initialized.")
