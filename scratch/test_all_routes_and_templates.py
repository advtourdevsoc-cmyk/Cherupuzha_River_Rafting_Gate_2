import os
import sys
sys.path.insert(0, os.path.abspath('.'))
import traceback
from datetime import datetime, date, timezone
from bson import ObjectId

# Create mock DB for offline Flask test execution
class MockCursor:
    def __init__(self, data):
        self.data = list(data)
    def sort(self, *args, **kwargs):
        return self
    def limit(self, n):
        return MockCursor(self.data[:n])
    def __iter__(self):
        return iter(self.data)
    def __list__(self):
        return list(self.data)

class MockCollection:
    def __init__(self):
        self.docs = []

    def find(self, query=None, projection=None, sort=None):
        query = query or {}
        results = []
        for d in self.docs:
            match = True
            for k, v in query.items():
                if k == '_id':
                    if isinstance(v, dict) and '$in' in v:
                        if d.get('_id') not in v['$in']:
                            match = False
                    elif d.get('_id') != v:
                        match = False
                elif k == 'date':
                    if isinstance(v, dict):
                        if '$gte' in v and (d.get('date') or '') < v['$gte']:
                            match = False
                        if '$lte' in v and (d.get('date') or '') > v['$lte']:
                            match = False
                        if '$in' in v and d.get('date') not in v['$in']:
                            match = False
                    elif d.get('date') != v:
                        match = False
                elif k == 'status':
                    if isinstance(v, dict) and '$in' in v:
                        if d.get('status') not in v['$in']:
                            match = False
                    elif isinstance(v, dict) and '$ne' in v:
                        if d.get('status') == v['$ne']:
                            match = False
                    elif d.get('status') != v:
                        match = False
                elif k == 'payment_status':
                    if isinstance(v, dict) and '$ne' in v:
                        if d.get('payment_status') == v['$ne']:
                            match = False
                    elif d.get('payment_status') != v:
                        match = False
                elif k == 'razorpay_order_id':
                    if isinstance(v, dict):
                        if '$exists' in v and v['$exists'] and not d.get('razorpay_order_id'):
                            match = False
                        if '$ne' in v and d.get('razorpay_order_id') == v['$ne']:
                            match = False
                    elif d.get('razorpay_order_id') != v:
                        match = False
                elif d.get(k) != v:
                    match = False
            if match:
                results.append(d)
        return MockCursor(results)

    def find_one(self, query=None):
        cursor = self.find(query)
        data = list(cursor)
        return data[0] if data else None

    def find_one_and_update(self, query, update, return_document=None):
        doc = self.find_one(query)
        if doc:
            if '$set' in update:
                doc.update(update['$set'])
            return doc
        return None

    def insert_one(self, doc):
        if '_id' not in doc:
            doc['_id'] = ObjectId()
        self.docs.append(doc)
        class Res:
            inserted_id = doc['_id']
        return Res()

    def insert_many(self, docs):
        for doc in docs:
            self.insert_one(doc)

    def update_one(self, query, update, upsert=False):
        doc = self.find_one(query)
        if doc:
            if '$set' in update:
                doc.update(update['$set'])
        elif upsert:
            new_doc = dict(query)
            if '$set' in update:
                new_doc.update(update['$set'])
            self.insert_one(new_doc)

    def update_many(self, query, update):
        for doc in self.find(query):
            if '$set' in update:
                doc.update(update['$set'])

    def delete_many(self, query):
        to_del = list(self.find(query))
        for d in to_del:
            if d in self.docs:
                self.docs.remove(d)
        class Res:
            deleted_count = len(to_del)
        return Res()

    def delete_one(self, query):
        doc = self.find_one(query)
        if doc and doc in self.docs:
            self.docs.remove(doc)
        class Res:
            deleted_count = 1 if doc else 0
        return Res()

    def count_documents(self, query=None):
        return len(list(self.find(query)))

    def replace_one(self, query, doc, upsert=False):
        existing = self.find_one(query)
        if existing:
            self.docs.remove(existing)
        self.docs.append(doc)

class MockDatabase:
    def __init__(self):
        self.bookings = MockCollection()
        self.rafts = MockCollection()
        self.users = MockCollection()
        self.settings = MockCollection()
        self.payments = MockCollection()
        self.coupons = MockCollection()
        self.admin_audit_logs = MockCollection()

        # Insert default system_settings
        self.settings.insert_one({
            '_id': 'system_settings',
            'start_date': '2026-10-01',
            'end_date': '2026-10-31',
            'days': 30,
            'slots': 4,
            'rafts_per_slot': 5,
            'capacity': 6,
            'time_slots': ['7:00am', '10:00am', '1:00pm', '3:30pm'],
            'weekday_amount': 1150,
            'saturday_amount': 1350,
            'weekday_advance_percent': 25.0,
            'saturday_advance_percent': 25.0,
            'platform_fee': 2.4
        })

class MockUser:
    def __init__(self, role='supreme_admin'):
        self.id = '123456789012345678901234'
        self._id = ObjectId(self.id)
        self.email = 'admin@example.com'
        self.role = role
        self.is_authenticated = True
        self.is_active_flag = True

    def is_authenticated(self):
        return True
    def is_active(self):
        return True
    def is_anonymous(self):
        return False
    def get_id(self):
        return self.id
    def is_admin(self):
        return self.role in ['admin', 'supreme_admin']
    def is_supreme_admin(self):
        return self.role == 'supreme_admin'
    def is_subadmin(self):
        return self.role == 'subadmin'
    def is_admin_or_subadmin(self):
        return self.role in ['admin', 'subadmin', 'supreme_admin']

def run_test():
    from app import create_app
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    mock_db = MockDatabase()
    class MongoNS:
        db = mock_db
        client = None
    app.mongo = MongoNS()

    # Populate mock bookings with edge case data
    test_bookings = [
        # 1. Normal Confirmed Booking
        {
            '_id': ObjectId(),
            'user_id': 'user1@gmail.com',
            'user_name': 'Test User 1',
            'email': 'user1@gmail.com',
            'phone': '9876543210',
            'date': '2026-10-08',
            'slot': '7:00am',
            'group_size': 5,
            'status': 'Confirmed',
            'payment_status': 'Paid',
            'amount': 5750.0,
            'total_amount': 5750.0,
            'advance_amount': 1437.5,
            'balance_amount': 4312.5,
            'created_at': datetime.now(timezone.utc),
            'raft_allocations': [1],
            'raft_allocation_details': [{'raft_id': 1, 'count': 5}]
        },
        # 2. Visited Booking (new booking or completed status)
        {
            '_id': ObjectId(),
            'user_id': 'user2@gmail.com',
            'user_name': 'Visited User',
            'email': 'user2@gmail.com',
            'phone': '9876543211',
            'date': '2026-10-08',
            'slot': '10:00am',
            'group_size': 4,
            'status': 'Visited',
            'payment_status': 'Pending',
            'amount': 4600.0,
            'total_amount': 4600.0,
            'advance_amount': 1150.0,
            'balance_amount': 3450.0,
            'created_at': datetime.now(timezone.utc),
        },
        # 3. Legacy / Incomplete Booking missing fields (None fields)
        {
            '_id': ObjectId(),
            'user_id': None,
            'name': 'Legacy Booking',
            'email': None,
            'phone': None,
            'date': '2026-10-15',
            'slot': '1:00pm',
            'group_size': None,
            'status': None,
            'payment_status': None,
            'amount': None,
            'total_amount': None,
            'advance_amount': None,
            'balance_amount': None,
            'created_at': None
        },
        # 4. Future Date Booking
        {
            '_id': ObjectId(),
            'user_id': 'future@gmail.com',
            'user_name': 'Future User',
            'email': 'future@gmail.com',
            'phone': '9876543212',
            'date': '2026-10-25',
            'slot': '3:30pm',
            'group_size': 6,
            'status': 'Confirmed',
            'payment_status': 'Paid',
            'amount': 6900.0,
            'advance_amount': 1725.0,
            'balance_amount': 5175.0,
            'created_at': datetime.now(timezone.utc)
        },
        # 5. Cancelled Booking
        {
            '_id': ObjectId(),
            'user_id': 'cancel@gmail.com',
            'user_name': 'Cancelled User',
            'email': 'cancel@gmail.com',
            'phone': '9876543213',
            'date': '2026-10-08',
            'slot': '7:00am',
            'group_size': 2,
            'status': 'Cancelled',
            'payment_status': 'Failed',
            'amount': 2300.0,
            'created_at': datetime.now(timezone.utc)
        },
        # 6. Pending Booking
        {
            '_id': ObjectId(),
            'user_id': 'pending@gmail.com',
            'user_name': 'Pending User',
            'email': 'pending@gmail.com',
            'phone': '9876543214',
            'date': '2026-10-09',
            'slot': '10:00am',
            'group_size': 3,
            'status': 'Pending',
            'payment_status': 'Pending',
            'amount': 3450.0,
            'created_at': datetime.now(timezone.utc)
        }
    ]

    for b in test_bookings:
        mock_db.bookings.insert_one(b)

    client = app.test_client()

    print("=== Testing Public Routes ===")
    public_urls = [
        '/',
        '/check_coupon?phone=9876543210&email=user1@gmail.com&group_size=5&date=2026-10-08',
        '/book',
        '/availability',
        '/slot_availability?day=2026-10-08',
        '/fully_booked_dates',
        '/track-booking',
        '/login'
    ]

    for url in public_urls:
        res = client.get(url)
        print(f"GET {url} -> {res.status_code}")
        if res.status_code == 500:
            print(f"[FAIL 500] Public GET {url}")

    print("\n=== Testing Admin Routes (Logged in as Admin) ===")
    with client.session_transaction() as sess:
        sess['_user_id'] = '123456789012345678901234'
        sess['_fresh'] = True

    # Set user_loader on app.login_manager
    @app.login_manager.user_loader
    def load_user(user_id):
        return MockUser('supreme_admin')

    admin_urls = [
        '/admin/dashboard',
        '/admin/dashboard?from=2026-10-01&to=2026-10-31',
        '/admin/dashboard?status=Visited',
        '/admin/dashboard?status=all',
        '/admin/dashboard?status=Pending',
        '/admin/dashboard?status=Confirmed',
        '/admin/dashboard?status=Cancelled',
        '/admin/dashboard?slot=7:00am',
        '/admin/calendar',
        '/admin/settings',
        '/admin/occupancy_data?day=2026-10-08',
        '/admin/occupancy_by_date?day=2026-10-08',
        '/admin/occupancy_detail?from=2026-10-08&to=2026-10-10',
        '/admin/api/export_bookings?from=2026-10-01&to=2026-10-31',
        '/admin/api/export_bookings?status=Visited',
        '/admin/api/export_bookings?status=all',
        '/admin/manage_users',
        '/admin/coupons'
    ]

    for url in admin_urls:
        try:
            res = client.get(url)
            print(f"GET {url} -> {res.status_code}")
            if res.status_code == 500:
                print(f"[FAIL 500] Admin GET {url}")
                print(res.get_data(as_text=True)[:500])
        except Exception as e:
            print(f"[EXCEPTION] GET {url}: {e}")
            traceback.print_exc()

    print("\n=== Testing Subadmin Routes ===")
    @app.login_manager.user_loader
    def load_subadmin(user_id):
        return MockUser('subadmin')

    subadmin_urls = [
        '/admin/dashboard',
        '/admin/occupancy_data?day=2026-10-08',
        '/admin/occupancy_detail?from=2026-10-08&to=2026-10-08'
    ]

    for url in subadmin_urls:
        try:
            res = client.get(url)
            print(f"GET {url} (subadmin) -> {res.status_code}")
            if res.status_code == 500:
                print(f"[FAIL 500] Subadmin GET {url}")
        except Exception as e:
            print(f"[EXCEPTION] Subadmin GET {url}: {e}")
            traceback.print_exc()

if __name__ == '__main__':
    run_test()
