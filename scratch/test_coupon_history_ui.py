import os
import sys
import unittest
from bson import ObjectId
from app import create_app
from flask import render_template
from flask_login import login_user
from models.user_model import User

class TestCouponHistoryUI(unittest.TestCase):
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
                'email': 'admin_test_coupon_ui@raft.com',
                'role': 'supreme_admin'
            }
            self.db.users.insert_one(self.admin_doc)

    def test_coupon_history_rendering_route(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # 1. Section Header & Badges
        self.assertIn('Coupon History', html)
        self.assertIn('id="coupon-count-badge"', html)
        self.assertNotIn('← Dashboard', html)
        self.assertIn('id="toggle-coupon-filter-btn"', html)
        self.assertIn('id="toggle-coupon-delete-range-btn"', html)

        # 2. Section Layout matching Create Booking page
        self.assertIn('p-6 space-y-4', html)
        self.assertIn('p-4 bg-gray-50 rounded-xl border border-gray-200 shadow-sm', html)
        self.assertIn('overflow-x-auto rounded-lg border border-gray-200', html)

        # 3. Filters
        self.assertIn('id="historyStartDate"', html)
        self.assertIn('id="historyEndDate"', html)
        self.assertIn('id="historyStatus"', html)
        self.assertIn('id="historySearch"', html)
        self.assertIn('deleteCouponsByDateRange()', html)
        self.assertIn('clearDateFilter()', html)

        # 4. Table structure
        self.assertIn('id="couponTable"', html)
        self.assertIn('id="couponTableBody"', html)
        self.assertIn('Coupon Code', html)
        self.assertIn('Customer Phone', html)
        self.assertIn('Group Size', html)
        self.assertIn('Discount', html)
        self.assertIn('Applicable Days', html)
        self.assertIn('Customer Email', html)
        self.assertIn('Generated Date', html)
        self.assertIn('Status', html)
        self.assertIn('Action', html)

    def test_coupon_history_template_render_with_mock_data(self):
        user = User(self.admin_doc)
        with self.app.test_request_context('/admin/coupons'):
            login_user(user)
            mock_coupons = [
                {
                    '_id': 'coupon123',
                    'code': 'PROMO20',
                    'phone': '9876543210',
                    'group_size': 5,
                    'discount_type': 'per_head',
                    'discount_value': 100,
                    'valid_day_types': ['weekday'],
                    'email': 'customer@gmail.com',
                    'created_at_ist': '10 Oct 2026, 02:30 PM',
                    'created_date_iso': '2026-10-10',
                    'is_revoked': False,
                    'is_deleted': False,
                    'is_used': False,
                    'is_expired': False
                },
                {
                    '_id': 'coupon456',
                    'code': 'FLAT500',
                    'phone': '9123456789',
                    'group_size': 8,
                    'discount_type': 'flat',
                    'discount_value': 500,
                    'valid_day_types': ['weekend'],
                    'email': 'vip@gmail.com',
                    'created_at_ist': '09 Oct 2026, 11:00 AM',
                    'created_date_iso': '2026-10-09',
                    'is_revoked': False,
                    'is_deleted': False,
                    'is_used': True,
                    'is_expired': False
                }
            ]
            html = render_template('admin_coupons.html', coupons=mock_coupons)

            # Check count badge
            self.assertIn('2 Coupons', html)

            # Check preserved content & badges
            self.assertIn('PROMO20', html)
            self.assertIn('FLAT500', html)
            self.assertIn('+91 98765 43210', html)
            self.assertIn('+91 91234 56789', html)
            self.assertIn('customer@gmail.com', html)
            self.assertIn('vip@gmail.com', html)
            self.assertIn('₹100.00/head', html)
            self.assertIn('₹500.00 Flat', html)
            self.assertIn('Mon–Fri', html)
            self.assertIn('Sat–Sun', html)
            self.assertIn('Valid', html)
            self.assertIn('Used', html)
            self.assertIn("deleteSingleCoupon('coupon123', 'PROMO20')", html)
            self.assertIn("deleteSingleCoupon('coupon456', 'FLAT500')", html)

    def test_coupon_history_template_render_empty(self):
        user = User(self.admin_doc)
        with self.app.test_request_context('/admin/coupons'):
            login_user(user)
            html = render_template('admin_coupons.html', coupons=[])
            self.assertIn('id="emptyRow"', html)
            self.assertIn('No coupons generated yet', html)
            self.assertIn('colspan="9"', html)
            self.assertIn('0 Coupons', html)

if __name__ == '__main__':
    unittest.main()
