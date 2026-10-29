import sys
import traceback
import mongomock
from flask import Flask
from app import create_app

def test_routes():
    print("Initializing app with mock mongo...")
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    
    # Replace app.mongo with a mongomock client for safety during exhaustive testing
    mock_client = mongomock.MongoClient()
    db = mock_client['raft_booking']
    app.mongo.client = mock_client
    app.mongo.db = db

    client = app.test_client()

    print("Testing GET routes...")
    # Public routes
    routes_to_test = [
        '/',
        '/check_coupon',
        '/availability?date=2026-10-08',
        '/slot_availability?date=2026-10-08',
        '/fully_booked_dates',
        '/track-booking',
        '/login',
        '/health'
    ]

    for route in routes_to_test:
        try:
            res = client.get(route)
            print(f"GET {route} -> Status: {res.status_code}")
            if res.status_code == 500:
                print(f"[CRITICAL] 500 error on GET {route}!")
        except Exception as e:
            print(f"[EXCEPTION] GET {route} -> {e}")
            traceback.print_exc()

if __name__ == '__main__':
    test_routes()
