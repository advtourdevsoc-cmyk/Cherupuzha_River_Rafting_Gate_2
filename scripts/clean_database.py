"""
Clean Database Script
Drops all collections from the database and reinitializes with default data
"""
from pymongo import MongoClient
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import sys
import os

# Load environment variables
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import MONGO_URI as mongo_uri

# Connect to MongoDB
client = MongoClient(mongo_uri)
db = client.get_database("raft_booking")

print("🧹 Starting database cleanup...")
print(f"📍 Database: {db.name}")

# Drop the entire database
print("\n🗑️  Dropping entire database...")
try:
    client.drop_database("raft_booking")
    print("   ✅ Database dropped completely")
except Exception as e:
    print(f"   ⚠️  Warning: {e}")

# Reconnect to get fresh database
db = client.get_database("raft_booking")

print("\n✨ Database cleaned successfully!")

# Reinitialize with default data
print("\n🔄 Reinitializing database with default data...")

# Create admin user
supreme_admin_email = os.environ.get("SUPREME_ADMIN_EMAIL")
supreme_admin_password = os.environ.get("SUPREME_ADMIN_PASSWORD")

if not supreme_admin_email or not supreme_admin_password:
    print("⚠️  WARNING: SUPREME_ADMIN_EMAIL and SUPREME_ADMIN_PASSWORD not set in .env")
    print("    Skipping admin user creation")
else:
    hashed_pw = generate_password_hash(supreme_admin_password)
    db.users.insert_one({
        "name": "Admin",
        "email": supreme_admin_email,
        "phone": "0000000000",
        "role": "supreme_admin",
        "password_hash": hashed_pw
    })
    print(f"✅ Supreme Admin user created")
    print(f"   Email: {supreme_admin_email}")

# Create default system settings
settings = {
    "_id": "system_settings",
    "days": 30,
    "slots": 4,
    "rafts_per_slot": 5,
    "capacity": 6,
    "time_slots": ["7:00am", "10:00am", "1:00pm", "3:30pm"],
    "platform_fee": 0,
    "weekday_amount": 500,
    "saturday_amount": 700,
    "weekday_advance_percent": 25,
    "saturday_advance_percent": 35
}
db.settings.insert_one(settings)
print(f"✅ Default system settings created")

print("\n✨ Database reinitialized successfully!")
print("\n📊 Final state:")
print(f"   Collections: {len(db.list_collection_names())}")
print(f"   User count: {db.users.count_documents({})}")
print(f"   Settings: {db.settings.count_documents({})}")
