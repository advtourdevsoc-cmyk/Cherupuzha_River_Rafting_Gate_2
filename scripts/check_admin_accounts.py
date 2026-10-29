"""
Check all admin and subadmin accounts in database
"""
from pymongo import MongoClient
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

print("📋 Current Admin & Subadmin Accounts:\n")

# Find all admins
admins = list(db.users.find({'role': 'admin'}))
print(f"👤 Admin Accounts ({len(admins)}):")
for admin in admins:
    print(f"   - {admin.get('email')} (Name: {admin.get('name')})")

# Find all subadmins
subadmins = list(db.users.find({'role': 'subadmin'}))
print(f"\n🔑 Subadmin Accounts ({len(subadmins)}):")
for subadmin in subadmins:
    print(f"   - {subadmin.get('email')} (Name: {subadmin.get('name')})")

print("\n✅ Done!")
client.close()
