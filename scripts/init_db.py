from pymongo import MongoClient
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import sys
import os
# Load environment variables from .env file
load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import MONGO_URI as mongo_uri
client = MongoClient(mongo_uri)
db = client.get_database("raft_booking")
supreme_admin_email = os.environ.get("SUPREME_ADMIN_EMAIL")
supreme_admin_password = os.environ.get("SUPREME_ADMIN_PASSWORD")

# Check if credentials are set
if not supreme_admin_email or not supreme_admin_password:
    print("❌ ERROR: SUPREME_ADMIN_EMAIL and SUPREME_ADMIN_PASSWORD must be set in .env file")
    sys.exit(1)

hashed_pw = generate_password_hash(supreme_admin_password)
existing = db.users.find_one({"email": supreme_admin_email})
if existing:
    db.users.update_many({"email": supreme_admin_email}, {"$set": {"password_hash": hashed_pw, "role":"supreme_admin", "name":"Admin", "phone":"0000000000"}})
    print(f"[OK] Supreme Admin user updated (email: {supreme_admin_email})")
else:
    db.users.insert_one({"name":"Admin", "email": supreme_admin_email, "phone":"0000000000", "role":"supreme_admin", "password_hash": hashed_pw})
    print(f"[OK] Supreme Admin user created (email: {supreme_admin_email}, password: {supreme_admin_password})")
settings = {
    "_id":"system_settings",
    "days":30,
    "slots":4,
    "rafts_per_slot":5,
    "capacity":6,
    "time_slots":["7:00am","10:00am","1:00pm","3:30pm"],
    "platform_fee":0
}
db.settings.replace_one({"_id":"system_settings"}, settings, upsert=True)
print("[OK] Default system settings inserted/updated.")
