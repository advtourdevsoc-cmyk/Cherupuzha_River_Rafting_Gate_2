import unittest
from bson import ObjectId
import datetime
from app import create_app
from models.booking_model import create_booking, update_booking_status

class TestAdminBookingIndicator(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.db = self.app.mongo.db

        existing_admin = self.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            self.admin_id = existing_admin['_id']
            self.admin_doc = existing_admin
        else:
            self.admin_id = ObjectId()
            self.admin_doc = {
                '_id': self.admin_id,
                'name': 'Test Admin',
                'email': 'admin_indicator_test@raft.com',
                'role': 'supreme_admin'
            }
            self.db.users.insert_one(self.admin_doc)

        self.created_booking_ids = []

    def tearDown(self):
        if self.created_booking_ids:
            self.db.bookings.delete_many({'_id': {'$in': self.created_booking_ids}})

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def test_1_admin_created_booking_displays_admin_indicator(self):
        """Admin booking created via form displays 'Admin Booking' indicator badge."""
        self._login_admin()
        test_date = datetime.date.today().isoformat()

        booking_details = {
            'name': 'Admin Created Guest',
            'email': 'adminguest@test.com',
            'phone': '9988112233',
            'location': 'Kannur',
            'date': test_date,
            'slot': '7:00am',
            'group_size': 4,
            'amount_per_person': 700.0,
            'price_amount': 2800.0,
            'total_amount': 2800.0,
            'advance_amount': 700.0,
            'balance_amount': 2100.0,
            'is_admin_booking': True,
            'payment_mode': 'Cash'
        }

        b_id = create_booking(
            self.db,
            user_id='adminguest@test.com',
            booking_details=booking_details,
            amount=2800.0,
            currency='INR',
            status='Confirmed',
            payment_status='Paid',
            allow_payment=True,
            razorpay_order_id='ADMIN_TEST_123'
        )
        self.created_booking_ids.append(b_id)
        update_booking_status(
            self.db,
            b_id,
            status='Confirmed',
            payment_status='Paid',
            extra_updates={'is_admin_booking': True}
        )

        # GET /admin/dashboard
        res = self.client.get(f'/admin/dashboard?from={test_date}&to={test_date}&status=Confirmed')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('Admin Created Guest', html)
        self.assertIn('Admin Booking', html)
        self.assertIn('bg-blue-100', html)

    def test_2_customer_booking_does_not_display_admin_indicator(self):
        """Customer booking without admin origin does NOT display the 'Admin Booking' indicator."""
        self._login_admin()
        test_date = datetime.date.today().isoformat()

        customer_booking_details = {
            'name': 'Pure Customer Guest',
            'email': 'purecustomer@test.com',
            'phone': '9988223344',
            'location': 'Kannur',
            'date': test_date,
            'slot': '7:00am',
            'group_size': 4,
            'amount_per_person': 700.0,
            'price_amount': 2800.0,
            'total_amount': 2800.0,
            'advance_amount': 700.0,
            'balance_amount': 2100.0,
        }

        b_id = create_booking(
            self.db,
            user_id='purecustomer@test.com',
            booking_details=customer_booking_details,
            amount=2800.0,
            currency='INR',
            status='Confirmed',
            payment_status='Paid',
            allow_payment=True
        )
        self.created_booking_ids.append(b_id)

        res = self.client.get(f'/admin/dashboard?from={test_date}&to={test_date}&status=Confirmed')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('Pure Customer Guest', html)
        # Find row for Pure Customer Guest
        name_pos = html.find('Pure Customer Guest')
        self.assertNotEqual(name_pos, -1)
        # Within the containing cell (next 300 chars), there should NOT be Admin Booking badge
        cell_snippet = html[name_pos:name_pos + 400]
        self.assertNotIn('Admin Booking', cell_snippet)
        self.assertNotIn('bg-blue-100', cell_snippet)

    def test_3_customer_coupon_booking_retains_coupon_and_no_admin_indicator(self):
        """Customer booking with coupon shows coupon indicator only, no admin indicator."""
        self._login_admin()
        test_date = datetime.date.today().isoformat()

        coupon_booking_details = {
            'name': 'Coupon Only Guest',
            'email': 'coupononly@test.com',
            'phone': '9988334455',
            'location': 'Kannur',
            'date': test_date,
            'slot': '7:00am',
            'group_size': 4,
            'amount_per_person': 600.0,
            'price_amount': 2400.0,
            'total_amount': 2400.0,
            'advance_amount': 600.0,
            'balance_amount': 1800.0,
            'coupon_code': 'RAFT-CUST1',
            'coupon_discount_applied': 400.0
        }

        b_id = create_booking(
            self.db,
            user_id='coupononly@test.com',
            booking_details=coupon_booking_details,
            amount=2400.0,
            currency='INR',
            status='Confirmed',
            payment_status='Paid',
            allow_payment=True
        )
        self.created_booking_ids.append(b_id)

        res = self.client.get(f'/admin/dashboard?from={test_date}&to={test_date}&status=Confirmed')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('Coupon Only Guest', html)
        name_pos = html.find('Coupon Only Guest')
        cell_snippet = html[name_pos:name_pos + 500]

        # Coupon badge present
        self.assertIn('RAFT-CUST1', cell_snippet)
        self.assertIn('bg-emerald-100', cell_snippet)
        # Admin indicator NOT present
        self.assertNotIn('Admin Booking', cell_snippet)
        self.assertNotIn('bg-blue-100', cell_snippet)

    def test_4_admin_coupon_booking_displays_both_indicators_independently(self):
        """Admin booking that also used a coupon displays BOTH indicators independently."""
        self._login_admin()
        test_date = datetime.date.today().isoformat()

        both_booking_details = {
            'name': 'Admin Plus Coupon Guest',
            'email': 'bothguest@test.com',
            'phone': '9988445566',
            'location': 'Kannur',
            'date': test_date,
            'slot': '7:00am',
            'group_size': 4,
            'amount_per_person': 600.0,
            'price_amount': 2400.0,
            'total_amount': 2400.0,
            'advance_amount': 600.0,
            'balance_amount': 1800.0,
            'is_admin_booking': True,
            'coupon_code': 'RAFT-BOTH9',
            'coupon_discount_applied': 400.0,
            'payment_mode': 'Cash'
        }

        b_id = create_booking(
            self.db,
            user_id='bothguest@test.com',
            booking_details=both_booking_details,
            amount=2400.0,
            currency='INR',
            status='Confirmed',
            payment_status='Paid',
            allow_payment=True,
            razorpay_order_id='ADMIN_TEST_BOTH'
        )
        self.created_booking_ids.append(b_id)
        update_booking_status(
            self.db,
            b_id,
            status='Confirmed',
            payment_status='Paid',
            extra_updates={'is_admin_booking': True}
        )

        res = self.client.get(f'/admin/dashboard?from={test_date}&to={test_date}&status=Confirmed')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('Admin Plus Coupon Guest', html)
        name_pos = html.find('Admin Plus Coupon Guest')
        cell_snippet = html[name_pos:name_pos + 600]

        # Both indicators present
        self.assertIn('Admin Booking', cell_snippet)
        self.assertIn('bg-blue-100', cell_snippet)
        self.assertIn('RAFT-BOTH9', cell_snippet)
        self.assertIn('bg-emerald-100', cell_snippet)

    def test_5_full_admin_booking_form_post_flow_displays_indicator_in_all_bookings(self):
        """End-to-end: Creating booking via POST /admin/booking/create reflects in All Bookings."""
        self._login_admin()
        test_date = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        test_phone = '9999888877'

        post_data = {
            'name': 'E2E Admin Created User',
            'phone': test_phone,
            'email': 'e2eadmin@gmail.com',
            'location': 'Kannur',
            'booking_date': test_date,
            'slot': '7:00am',
            'group_size': '4',
            'payment_mode': 'Cash',
            'custom_advance_amount': '500',
            'admin_notes': 'E2E test note'
        }

        res_post = self.client.post('/admin/booking/create', data=post_data, follow_redirects=False)
        self.assertEqual(res_post.status_code, 302)

        # Retrieve created booking to track for cleanup
        booking = self.db.bookings.find_one({'phone': test_phone, 'date': test_date})
        self.assertIsNotNone(booking)
        self.created_booking_ids.append(booking['_id'])
        self.assertTrue(booking.get('is_admin_booking'))

        # Check All Bookings dashboard
        res_dash = self.client.get(f'/admin/dashboard?from={test_date}&to={test_date}&status=Confirmed')
        self.assertEqual(res_dash.status_code, 200)
        html = res_dash.get_data(as_text=True)

        self.assertIn('E2E Admin Created User', html)
        name_pos = html.find('E2E Admin Created User')
        cell_snippet = html[name_pos:name_pos + 400]
        self.assertIn('Admin Booking', cell_snippet)
        self.assertIn('bg-blue-100', cell_snippet)

    def test_6_subadmin_dashboard_today_table_shows_admin_indicator(self):
        """Sub-admin view (today table) also renders Admin Booking badge."""
        subadmin = self.db.users.find_one({'role': 'subadmin'})
        if not subadmin:
            subadmin = {
                '_id': ObjectId(),
                'name': 'Test Subadmin',
                'email': 'subadmin_test@raft.com',
                'role': 'subadmin'
            }
            self.db.users.insert_one(subadmin)

        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(subadmin['_id'])
            sess['_fresh'] = True

        today_str = datetime.date.today().isoformat()
        b_details = {
            'name': 'Subadmin Table Admin Booking',
            'email': 'subadmintest@test.com',
            'phone': '9988776611',
            'location': 'Kannur',
            'date': today_str,
            'slot': '7:00am',
            'group_size': 2,
            'amount_per_person': 700.0,
            'price_amount': 1400.0,
            'total_amount': 1400.0,
            'advance_amount': 350.0,
            'balance_amount': 1050.0,
            'is_admin_booking': True,
            'payment_mode': 'Cash'
        }

        b_id = create_booking(
            self.db,
            user_id='subadmintest@test.com',
            booking_details=b_details,
            amount=1400.0,
            currency='INR',
            status='Confirmed',
            payment_status='Paid',
            allow_payment=True
        )
        self.created_booking_ids.append(b_id)
        update_booking_status(
            self.db,
            b_id,
            status='Confirmed',
            payment_status='Paid',
            extra_updates={'is_admin_booking': True}
        )

        res = self.client.get('/admin/dashboard')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('Subadmin Table Admin Booking', html)
        name_pos = html.find('Subadmin Table Admin Booking')
        cell_snippet = html[name_pos:name_pos + 400]
        self.assertIn('Admin Booking', cell_snippet)
        self.assertIn('bg-blue-100', cell_snippet)

if __name__ == '__main__':
    unittest.main()
