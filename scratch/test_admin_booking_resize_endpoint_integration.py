import os
import sys
import unittest
from bson import ObjectId
from datetime import date, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import create_app
from utils.allocation_logic import load_settings


class TestAdminBookingResizeEndpointIntegration(unittest.TestCase):
    def setUp(self):
        try:
            self.app = create_app()
            self.app.config['TESTING'] = True
            self.app.config['WTF_CSRF_ENABLED'] = False
            self.client = self.app.test_client()
            self.db = self.app.mongo.db
            self.db.command('ping')
        except Exception as e:
            self.skipTest(f"Live MongoDB instance not reachable: {e}")

        existing_admin = self.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            self.admin_id = existing_admin['_id']
        else:
            self.admin_id = ObjectId()
            self.db.users.insert_one({
                '_id': self.admin_id,
                'name': 'Test Supreme Admin',
                'email': 'supreme_resize_test@admin.com',
                'role': 'supreme_admin'
            })

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def test_full_admin_booking_create_and_resize_flow(self):
        """End-to-end test:
        1. Create admin booking with size 10, discount 100, advance 2000.
        2. Resize to 6 via POST /admin/reduce_group_size/<id>.
        3. Verify response and DB state: total 5400, advance 2000, balance 3400.
        4. Verify GET /admin/booking displays the updated figures.
        """
        self._login()
        settings = load_settings(self.db)
        booking_date_str = (date.today() + timedelta(days=28)).isoformat()
        slot = settings.get('time_slots', ['7:00am'])[0]

        # Clean any preexisting test data for this date
        self.db.bookings.delete_many({'date': booking_date_str})
        self.db.rafts.delete_many({'day': booking_date_str})

        # Ensure fresh rafts exist for test date and slot
        for i in range(1, 6):
            self.db.rafts.insert_one({
                'day': booking_date_str,
                'slot': slot,
                'raft_id': i,
                'occupancy': 0,
                'is_special': False
            })

        import time
        phone = f"98765{int(time.time()) % 100000:05d}"

        # 1. Create admin booking
        post_data = {
            'name': 'Integration Resize Guest',
            'phone': phone,
            'email': 'resizeguest@test.com',
            'location': 'Kannur',
            'booking_date': booking_date_str,
            'slot': slot,
            'group_size': '10',
            'per_person_discount': '100',
            'custom_advance_amount': '2000',
            'payment_mode': 'Cash',
            'admin_notes': 'Testing resize endpoint'
        }
        res_create = self.client.post('/admin/booking/create', data=post_data, follow_redirects=False)
        self.assertEqual(res_create.status_code, 302)

        # Retrieve created booking
        booking = self.db.bookings.find_one({'phone': phone, 'date': booking_date_str}, sort=[('created_at', -1)])
        self.assertIsNotNone(booking)
        booking_id = str(booking['_id'])
        self.assertEqual(booking['group_size'], 10)
        self.assertEqual(booking['advance_amount'], 2000.0)

        # 2. Resize to 6 via API
        res_resize = self.client.post(f'/admin/reduce_group_size/{booking_id}', json={'new_size': 6})
        self.assertEqual(res_resize.status_code, 200)
        resize_data = res_resize.get_json()
        self.assertNotIn('error', resize_data)
        self.assertEqual(resize_data.get('message'), 'Group size reduced successfully.')

        # 3. Verify DB state after resize
        updated_b = self.db.bookings.find_one({'_id': ObjectId(booking_id)})
        std_price = float(settings.get('weekday_amount', 500))
        expected_eff = std_price - 100.0
        expected_total = expected_eff * 6
        expected_balance = expected_total - 2000.0

        self.assertEqual(updated_b['group_size'], 6)
        self.assertEqual(updated_b['per_person_discount'], 100.0)
        self.assertEqual(updated_b['standard_amount_per_person'], std_price)
        self.assertEqual(updated_b['amount_per_person'], expected_eff)
        self.assertEqual(updated_b['total_amount'], expected_total)
        self.assertEqual(updated_b['advance_amount'], 2000.0)
        self.assertEqual(updated_b['balance_amount'], expected_balance)

        # 4. Verify admin booking history page displays updated amounts
        res_history = self.client.get(f'/admin/booking?from_date={date.today().isoformat()}&to_date={date.today().isoformat()}')
        self.assertEqual(res_history.status_code, 200)
        history_html = res_history.get_data(as_text=True)

        self.assertIn('Integration Resize Guest', history_html)
        self.assertIn(f'{expected_total:.2f}', history_html)
        self.assertIn('2000.00', history_html)
        self.assertIn(f'{expected_balance:.2f}', history_html)

        print("\n[SUCCESS] Integration test passed cleanly!")


if __name__ == '__main__':
    unittest.main()
