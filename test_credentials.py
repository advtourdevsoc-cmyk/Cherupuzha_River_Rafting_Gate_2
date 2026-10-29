#!/usr/bin/env python
"""Test script to verify .env file and MongoDB credentials"""

from dotenv import load_dotenv
import os
from pymongo import MongoClient
from werkzeug.security import check_password_hash

# Load environment variables
load_dotenv()

print("=" * 60)
print("TESTING .ENV FILE AND CREDENTIALS")
print("=" * 60)

# Check .env variables
print("\n[1] Checking Environment Variables:")
supreme_admin_email = os.environ.get('SUPREME_ADMIN_EMAIL')
supreme_admin_password = os.environ.get('SUPREME_ADMIN_PASSWORD')
mongo_uri = os.environ.get('MONGO_URI')

print(f"   SUPREME_ADMIN_EMAIL: {supreme_admin_email}")
print(f"   SUPREME_ADMIN_PASSWORD: {'*' * len(supreme_admin_password) if supreme_admin_password else 'NOT SET'}")
print(f"   MONGO_URI: {mongo_uri[:60]}..." if mongo_uri else "   MONGO_URI: NOT SET")

if not supreme_admin_email or not supreme_admin_password or not mongo_uri:
    print("\n[ERROR] Missing required environment variables in .env file")
    exit(1)

# Test MongoDB connection
print("\n[2] Testing MongoDB Connection:")
try:
    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    db = client.get_database('raft_booking')
    client.admin.command('ping')
    print("   [OK] MongoDB Connection: SUCCESS")
except Exception as e:
    print(f"   [FAIL] MongoDB Connection Failed: {str(e)}")
    exit(1)

# Check supreme admin user in database
print("\n[3] Checking Supreme Admin User in Database:")
admin_user = db.users.find_one({'email': supreme_admin_email})

if admin_user:
    print(f"   [OK] Supreme Admin User Found:")
    print(f"      Email: {admin_user.get('email')}")
    print(f"      Role: {admin_user.get('role')}")
    print(f"      Password Hash: {admin_user.get('password_hash')[:50]}..." if admin_user.get('password_hash') else "      Password Hash: NOT SET")
    
    # Test password verification
    print("\n[4] Testing Password Verification:")
    if admin_user.get('password_hash'):
        is_valid = check_password_hash(admin_user.get('password_hash'), supreme_admin_password)
        if is_valid:
            print(f"   [OK] Password is CORRECT - Login should work!")
        else:
            print(f"   [FAIL] Password is INCORRECT - Need to update it")
            print(f"      Current password in .env: {supreme_admin_password}")
    else:
        print("   [FAIL] No password hash stored in database")
else:
    print(f"   [FAIL] Supreme Admin user with email '{supreme_admin_email}' NOT found in database")
    print("\n   Available users in database:")
    for user in db.users.find():
        print(f"      - {user.get('email')} ({user.get('role')})")
    print("\n   [HINT] Run: python scripts/init_db.py")

print("\n" + "=" * 60)
