#!/usr/bin/env python
"""Test login endpoint"""

import requests
from dotenv import load_dotenv
import os

load_dotenv()

supreme_admin_email = os.environ.get('SUPREME_ADMIN_EMAIL')
supreme_admin_password = os.environ.get('SUPREME_ADMIN_PASSWORD')

print("=" * 60)
print("TESTING LOGIN ENDPOINT")
print("=" * 60)
print(f"\nTesting login with:")
print(f"  Email: {supreme_admin_email}")
print(f"  Password: {'*' * len(supreme_admin_password) if supreme_admin_password else 'NOT SET'}")

try:
    # Create a session to maintain cookies
    session = requests.Session()
    
    # Send login POST request
    login_data = {
        'email': supreme_admin_email,
        'password': supreme_admin_password
    }
    
    response = session.post(
        'http://127.0.0.1:5000/login',
        data=login_data,
        allow_redirects=False
    )
    
    print(f"\nResponse Status: {response.status_code}")
    
    if response.status_code == 302:
        print("[OK] Login successful! Redirected to:", response.headers.get('Location'))
    elif response.status_code == 200:
        print("[WARN] Got 200 response (login form shown)")
        if 'Invalid credentials' in response.text:
            print("[ERROR] Invalid credentials message shown")
        elif 'Admin only' in response.text:
            print("[ERROR] Admin only message shown")
        else:
            print("[INFO] Login form returned")
    else:
        print(f"[UNKNOWN] Unexpected status code: {response.status_code}")
        
except Exception as e:
    print(f"[ERROR] Error testing login: {str(e)}")
    print("\n[HINT] Make sure the app is running on http://127.0.0.1:5000")

print("\n" + "=" * 60)
