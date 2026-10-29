import os
import sys
import unittest
from bson import ObjectId
from app import create_app
from flask import url_for
from models.user_model import User

class TestCouponSeparatePageWorkflow(unittest.TestCase):
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
                'email': 'admin_coupon_separate_page@raft.com',
                'role': 'supreme_admin'
            }
            self.db.users.insert_one(self.admin_doc)

    def test_1_generate_coupon_button_links_to_separate_page(self):
        """1: Generate Coupon button on /admin/coupons links to /admin/coupons/create."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Check button ID and link
        self.assertIn('id="toggleGenerateFormBtn"', html)
        self.assertIn('href="/admin/coupons/create"', html)
        self.assertIn('Generate Coupon', html)
        self.assertNotIn('onclick="toggleCouponForm()"', html)

    def test_2_form_no_longer_appears_inline_on_coupon_generation_page(self):
        """2: The form does not appear inline on /admin/coupons."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        self.assertNotIn('id="generateCouponPageForm"', html)
        self.assertNotIn('id="coupon_phone"', html)
        self.assertNotIn('id="coupon_email"', html)
        self.assertNotIn('id="coupon_group_size"', html)
        self.assertNotIn('id="coupon_discount_value"', html)

    def test_3_separate_form_page_retains_original_form_ui(self):
        """3 & 4: /admin/coupons/create renders the exact form UI with all fields and back link."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons/create')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Top link
        self.assertIn('← Back to Coupon History', html)
        self.assertIn('href="/admin/coupons"', html)

        # Form card
        self.assertIn('id="couponFormContainer"', html)
        self.assertIn('Generate New Coupon', html)
        self.assertIn('Every generated coupon is strictly valid for single use.', html)

        # Form inputs
        self.assertIn('id="generateCouponPageForm"', html)
        self.assertIn('id="coupon_phone"', html)
        self.assertIn('id="coupon_email"', html)
        self.assertIn('id="coupon_group_size"', html)
        self.assertIn('name="valid_day_types"', html)
        self.assertIn('value="weekday"', html)
        self.assertIn('value="weekend"', html)
        self.assertIn('id="coupon_discount_value"', html)
        self.assertIn('id="coupon_discount_type"', html)
        self.assertIn('id="submitCouponBtn"', html)
        self.assertIn('Generate & Send Coupon', html)
        self.assertIn('Cancel', html)

    def test_4_coupon_generation_success_and_redirection(self):
        """5 & 6: Successful coupon creation via POST redirects to /admin/coupons and appears in history."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # Generate unique phone
        test_phone = "9887766554"
        # Clean up any existing coupon for this phone first
        self.db.coupons.delete_many({'phone': test_phone})

        post_data = {
            'phone': test_phone,
            'email': 'customer_test_page@gmail.com',
            'group_size': '6',
            'valid_day_types': ['weekday', 'weekend'],
            'discount_type': 'per_head',
            'discount_value': '150'
        }

        # Test POST to /admin/coupons/create
        res = self.client.post('/admin/coupons/create', data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Should be on /admin/coupons
        self.assertIn('Coupon History', html)
        # Should contain the phone formatted (+91 98877 66554)
        self.assertIn('98877 66554', html)
        self.assertIn('customer_test_page@gmail.com', html)
        self.assertIn('₹150.00/head', html)

        # Retrieve created coupon from DB to check exact code
        created_coupon = self.db.coupons.find_one({'phone': test_phone})
        self.assertIsNotNone(created_coupon)
        coupon_code = created_coupon['code']

        # Verify Success Popup Toast is displayed on the destination page
        self.assertIn('id="coupon-success-toast"', html)
        self.assertIn('Coupon Generated Successfully!', html)
        self.assertIn('Coupon Code:', html)
        self.assertIn(coupon_code, html)
        self.assertIn('₹150/head', html)
        self.assertIn('6 participants', html)
        self.assertIn('setTimeout', html)
        self.assertIn('4000', html)

        # Clean up
        self.db.coupons.delete_many({'phone': test_phone})

    def test_5_coupon_generation_failed_submission_shows_error(self):
        """7: Invalid submission stays on form page and displays error."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # Invalid email (not gmail)
        post_data = {
            'phone': '9887766555',
            'email': 'invalid_email@yahoo.com',
            'group_size': '6',
            'valid_day_types': ['weekday'],
            'discount_type': 'per_head',
            'discount_value': '100'
        }

        res = self.client.post('/admin/coupons/create', data=post_data, follow_redirects=False)
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Remains on the form page
        self.assertIn('id="generateCouponPageForm"', html)
        self.assertIn('Gmail', html)

    def test_6_refreshing_coupon_history_page_does_not_duplicate_coupon(self):
        """8: Refreshing returned page (GET /admin/coupons) does not create duplicate coupons."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        test_phone = "9887766556"
        self.db.coupons.delete_many({'phone': test_phone})

        post_data = {
            'phone': test_phone,
            'email': 'customer_refresh_test@gmail.com',
            'group_size': '4',
            'valid_day_types': ['weekday'],
            'discount_type': 'per_head',
            'discount_value': '100'
        }

        res = self.client.post('/admin/coupons/create', data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)

        initial_count = self.db.coupons.count_documents({'phone': test_phone})
        self.assertEqual(initial_count, 1)

        # Simulate F5 refresh on /admin/coupons
        res_refresh = self.client.get('/admin/coupons')
        self.assertEqual(res_refresh.status_code, 200)

        # Count must still be exactly 1
        after_count = self.db.coupons.count_documents({'phone': test_phone})
        self.assertEqual(after_count, 1)

        self.db.coupons.delete_many({'phone': test_phone})

    def test_7_existing_filter_and_delete_range_controls_continue_to_work(self):
        """9: Filter and Delete Range controls on /admin/coupons remain present and functional."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        self.assertIn('id="toggle-coupon-filter-btn"', html)
        self.assertIn('id="toggle-coupon-delete-range-btn"', html)
        self.assertIn('id="coupon-filter-panel"', html)
        self.assertIn('id="coupon-delete-range-panel"', html)
        self.assertIn('id="historyStartDate"', html)
        self.assertIn('id="historyEndDate"', html)
        self.assertIn('id="deleteStartDate"', html)
        self.assertIn('id="deleteEndDate"', html)

    def test_8_temporary_popup_appears_and_clears_on_refresh(self):
        """10: Popup appears once after generation and is cleared on subsequent GET."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        test_phone = "9887766557"
        self.db.coupons.delete_many({'phone': test_phone})

        post_data = {
            'phone': test_phone,
            'email': 'customer_toast_test@gmail.com',
            'group_size': '5',
            'valid_day_types': ['weekday', 'weekend'],
            'discount_type': 'per_head',
            'discount_value': '120'
        }

        # Step 1: Submit coupon generation
        res_post = self.client.post('/admin/coupons/create', data=post_data, follow_redirects=False)
        self.assertEqual(res_post.status_code, 302)
        self.assertTrue(res_post.headers['Location'].endswith('/admin/coupons'))

        # Step 2: Follow redirect to /admin/coupons -> Popup must be displayed
        res_redirect = self.client.get('/admin/coupons')
        self.assertEqual(res_redirect.status_code, 200)
        redirect_html = res_redirect.get_data(as_text=True)

        self.assertIn('id="coupon-success-toast"', redirect_html)
        self.assertIn('Coupon Generated Successfully!', redirect_html)

        coupon = self.db.coupons.find_one({'phone': test_phone})
        self.assertIsNotNone(coupon)
        self.assertIn(coupon['code'], redirect_html)

        # Step 3: Refresh / subsequent GET: Toast must be gone (not stale)
        res_refresh = self.client.get('/admin/coupons')
        self.assertEqual(res_refresh.status_code, 200)
        refresh_html = res_refresh.get_data(as_text=True)

        self.assertNotIn('id="coupon-success-toast"', refresh_html)

        self.db.coupons.delete_many({'phone': test_phone})

    def test_9_failed_coupon_generation_does_not_set_popup(self):
        """11: Failed coupon generation does not show success popup on form page or history page."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # Invalid phone
        post_data = {
            'phone': '',
            'email': 'valid@gmail.com',
            'group_size': '4',
            'valid_day_types': ['weekday'],
            'discount_type': 'per_head',
            'discount_value': '50'
        }

        res = self.client.post('/admin/coupons/create', data=post_data, follow_redirects=False)
        self.assertEqual(res.status_code, 200)
        form_html = res.get_data(as_text=True)
        self.assertNotIn('id="coupon-success-toast"', form_html)

        # Visiting coupon history page directly must NOT have any popup
        res_history = self.client.get('/admin/coupons')
        self.assertEqual(res_history.status_code, 200)
        history_html = res_history.get_data(as_text=True)
        self.assertNotIn('id="coupon-success-toast"', history_html)

if __name__ == '__main__':
    unittest.main()
