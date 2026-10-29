"""
Test Admin Booking Workflow with Temporary Success Notification
1. Access Admin Booking Page (/admin/booking)
2. Access Form Page (/admin/booking/create)
3. Submit Booking Form (POST to /admin/booking/create) -> Redirects to /admin/booking
4. Verify Temporary Success Toast on /admin/booking
5. Verify Refresh Dismisses Toast
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + '/..'))

import unittest
from datetime import date, timedelta
from app import create_app
from bson import ObjectId

class TestAdminBookingWorkflow(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client = self.app.test_client()
        self.db = self.app.mongo.db

        # Configure system settings
        self.settings = {
            '_id': 'system_settings',
            'days': 30,
            'slots': 4,
            'rafts_per_slot': 5,
            'capacity': 6,
            'weekday_amount': 500,
            'saturday_amount': 700,
            'time_slots': ['7:00am', '10:00am', '1:00pm', '3:30pm'],
            'platform_fee': 0,
            'normal_max_people_per_slot': 30,
            'bulk_max_people_per_slot': 30
        }
        self.db.settings.replace_one({'_id': 'system_settings'}, self.settings, upsert=True)

        if self.db.rafts.count_documents({}) == 0:
            for i in range(1, 6):
                self.db.rafts.insert_one({'raft_number': f'R{i}', 'capacity': 6, 'status': 'Active'})

    def test_admin_booking_redirect_and_notification(self):
        existing_admin = self.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            admin_id = existing_admin['_id']
        else:
            admin_id = ObjectId()
            self.db.users.insert_one({
                '_id': admin_id,
                'name': 'Workflow Test Admin',
                'email': 'admin_workflow_notif@test.com',
                'role': 'supreme_admin'
            })

        booking_date = (date.today() + timedelta(days=5)).isoformat()

        with self.client:
            with self.client.session_transaction() as sess:
                sess['_user_id'] = str(admin_id)
                sess['_fresh'] = True

            # 1. Access Admin Booking Page
            res_history = self.client.get('/admin/booking')
            self.assertEqual(res_history.status_code, 200)
            history_html = res_history.get_data(as_text=True)
            self.assertIn('/admin/booking/create', history_html)

            # 2. Access Admin Booking Form Page
            res_form = self.client.get('/admin/booking/create')
            self.assertEqual(res_form.status_code, 200)

            # 3. Submit Booking Form
            post_data = {
                'name': 'Toast Test Customer',
                'phone': '9555123456',
                'email': 'toastcustomer@gmail.com',
                'location': 'Kannur',
                'booking_date': booking_date,
                'slot': '7:00am',
                'group_size': '4',
                'custom_advance_amount': '1000',
                'payment_mode': 'Cash',
                'admin_notes': 'Testing toast flow'
            }

            res_post = self.client.post('/admin/booking/create', data=post_data, follow_redirects=False)
            self.assertEqual(res_post.status_code, 302)
            # Confirm redirected to /admin/booking, NOT to success page
            self.assertNotIn('/admin/booking/success/', res_post.headers['Location'])
            self.assertTrue(res_post.headers['Location'].endswith('/admin/booking'))

            # Verify booking saved in database
            booking = self.db.bookings.find_one({'phone': '9555123456', 'date': booking_date}, sort=[('created_at', -1)])
            self.assertIsNotNone(booking)
            self.assertEqual(booking['status'], 'Confirmed')

            # 4. Follow redirect to /admin/booking and verify toast notification
            res_redirect = self.client.get('/admin/booking')
            self.assertEqual(res_redirect.status_code, 200)
            redirect_html = res_redirect.get_data(as_text=True)

            self.assertIn('id="booking-success-toast"', redirect_html)
            self.assertIn('Booking Successful!', redirect_html)
            self.assertIn(str(booking['_id']), redirect_html)
            self.assertIn('Toast Test Customer', redirect_html)
            self.assertIn(booking_date, redirect_html)
            self.assertIn('7:00am', redirect_html)
            self.assertIn('4 participants', redirect_html)

            # 5. Refresh / subsequent GET: Toast must be gone
            res_refresh = self.client.get('/admin/booking')
            self.assertEqual(res_refresh.status_code, 200)
            refresh_html = res_refresh.get_data(as_text=True)
            self.assertNotIn('id="booking-success-toast"', refresh_html)

        print(f"\n[SUCCESS] TestAdminBookingWorkflow passed! Booking ID: {booking['_id']}")

if __name__ == '__main__':
    unittest.main()
