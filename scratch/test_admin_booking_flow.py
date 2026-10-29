"""
Test end-to-end Admin Booking creation flow without payment
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + '/..'))

import unittest
from datetime import date, timedelta
from app import create_app
from bson import ObjectId

class TestAdminBookingFlow(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.db = self.app.mongo.db

    def test_admin_booking_creation(self):
        # Create a supreme_admin user doc
        admin_doc = {
            '_id': ObjectId(),
            'name': 'Test Supreme Admin',
            'email': 'supreme@admin.com',
            'role': 'supreme_admin'
        }
        self.db.users.insert_one(admin_doc)

        today_str = date.today().isoformat()

        # Login supreme_admin using Flask-Login session or override
        with self.client:
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(admin_doc['_id'])
                sess['_fresh'] = True

            # Test calculation endpoint with custom advance
            calc_res = self.client.post('/admin/calculate_admin_booking_pricing', json={
                'date': today_str,
                'slot': '7:00am',
                'group_size': 5,
                'custom_advance': 1500
            })
            self.assertEqual(calc_res.status_code, 200)
            calc_data = calc_res.get_json()
            self.assertTrue(calc_data['success'])
            self.assertEqual(calc_data['final_advance'], 1500.0)

            # Test calculation endpoint with blank custom advance (should default to 0.0)
            calc_blank = self.client.post('/admin/calculate_admin_booking_pricing', json={
                'date': today_str,
                'slot': '7:00am',
                'group_size': 5,
                'custom_advance': ''
            })
            calc_blank_data = calc_blank.get_json()
            self.assertEqual(calc_blank_data['final_advance'], 0.0)
            self.assertEqual(calc_blank_data['balance_amount'], calc_blank_data['total_amount'])

            # Test calculation endpoint with excessive advance (50000 on a smaller total price)
            calc_excessive = self.client.post('/admin/calculate_admin_booking_pricing', json={
                'date': today_str,
                'slot': '7:00am',
                'group_size': 5,
                'custom_advance': 50000
            })
            calc_ex_data = calc_excessive.get_json()
            self.assertEqual(calc_ex_data['final_advance'], calc_ex_data['total_amount'])
            self.assertEqual(calc_ex_data['balance_amount'], 0.0)

            # Test POST creation of admin booking with custom advance
            post_data = {
                'name': 'John Admin Guest',
                'phone': '9876543210',
                'email': 'johnguest@gmail.com',
                'location': 'Kannur',
                'booking_date': today_str,
                'slot': '7:00am',
                'group_size': '5',
                'custom_advance_amount': '1500',
                'payment_mode': 'Cash',
                'admin_notes': 'Test booking'
            }

            response = self.client.post('/admin/booking', data=post_data, follow_redirects=True)
            self.assertEqual(response.status_code, 200)

        # Verify booking document inserted in DB
        booking = self.db.bookings.find_one({'phone': '9876543210', 'date': today_str})
        self.assertIsNotNone(booking)
        self.assertEqual(booking['status'], 'Confirmed')
        self.assertEqual(booking['payment_status'], 'Paid')
        self.assertEqual(booking['advance_amount'], 1500.0)
        self.assertEqual(booking['group_size'], 5)
        self.assertTrue(len(booking.get('raft_allocations', [])) > 0)
        self.assertTrue(booking.get('is_admin_booking'))

        print("\n[SUCCESS] TestAdminBookingFlow passed cleanly! Booking ID:", str(booking['_id']))

if __name__ == '__main__':
    unittest.main()
