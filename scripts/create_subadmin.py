from pymongo import MongoClient
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import sys
import os

# Load environment variables from .env file
load_dotenv()

try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from config import MONGO_URI
    print(f"Connecting to MongoDB Atlas...")
    
    client = MongoClient(MONGO_URI)
    db = client.get_database("raft_booking")
    
    # Read subadmin credentials from environment variables
    subadmin_email = os.environ.get("SUBADMIN_EMAIL")
    subadmin_password = os.environ.get("SUBADMIN_PASSWORD")
    
    # Check if credentials are provided
    if not subadmin_email or not subadmin_password:
        print("❌ ERROR: SUBADMIN_EMAIL and SUBADMIN_PASSWORD must be set in .env file")
        sys.exit(1)
    
    hashed_pw = generate_password_hash(subadmin_password)
    
    existing = db.users.find_one({"email": subadmin_email})
    if existing:
        db.users.update_one(
            {"email": subadmin_email}, 
            {
                "$set": {
                    "password_hash": hashed_pw, 
                    "role": "subadmin", 
                    "name": "Sub-Admin", 
                    "phone": "0000000000"
                }
            }
        )
        print(f"[OK] Sub-Admin user updated (email: {subadmin_email})")
        print(f"   Role: {existing.get('role', 'N/A')} -> subadmin")
    else:
        db.users.insert_one({
            "name": "Sub-Admin", 
            "email": subadmin_email, 
            "phone": "0000000000", 
            "role": "subadmin", 
            "password_hash": hashed_pw
        })
        print(f"[OK] Sub-Admin user created (email: {subadmin_email}, password: {subadmin_password})")
    
    # Verify the user was created/updated
    verify_user = db.users.find_one({"email": subadmin_email})
    if verify_user:
        print(f"[OK] Verification: User found with role '{verify_user.get('role')}'")
        print(f"   Email: {verify_user.get('email')}")
        print(f"   Name: {verify_user.get('name')}")
    else:
        print("[ERROR] User verification failed!")
        sys.exit(1)
    
    print("\n[OK] Sub-Admin user setup complete.")
    print(f"\nLogin credentials:")
    print(f"   Email: {subadmin_email}")
    print(f"   Password: {subadmin_password}")
    
except Exception as e:
    print(f"[ERROR] {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


