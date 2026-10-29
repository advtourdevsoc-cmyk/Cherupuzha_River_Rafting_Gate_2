"""
Remove admin@1234.com user from database
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

print("🧹 Removing admin@1234.com user...")

try:
    result = db.users.delete_one({"email": "admin@1234.com"})
    if result.deleted_count > 0:
        print(f"✅ Successfully removed admin@1234.com ({result.deleted_count} user deleted)")
    else:
        print("⚠️  No user found with email admin@1234.com")
except Exception as e:
    print(f"❌ Error: {e}")

client.close()
