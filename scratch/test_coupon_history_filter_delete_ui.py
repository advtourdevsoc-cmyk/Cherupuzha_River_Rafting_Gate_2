import os
import sys
import unittest
from bson import ObjectId
from app import create_app
from flask import render_template
from flask_login import login_user
from models.user_model import User

class TestCouponHistoryFilterDeleteUI(unittest.TestCase):
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
                'email': 'admin_coupon_filter_delete@raft.com',
                'role': 'supreme_admin'
            }
            self.db.users.insert_one(self.admin_doc)

    def test_1_dashboard_button_removed_and_replaced_with_delete_range_button(self):
        """1 & 2: ← Dashboard button is removed from Coupon History and replaced with Delete Range button matching styling."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Ensure ← Dashboard is not in the coupon history section
        self.assertNotIn('← Dashboard', html)

        # Ensure top Delete Range button is present with correct ID, onclick, label and styling
        self.assertIn('id="toggle-coupon-delete-range-btn"', html)
        self.assertIn('onclick="toggleCouponDeleteRangePanel()"', html)
        self.assertIn('Delete Range', html)
        self.assertIn('bg-red-600 hover:bg-red-700 active:bg-red-800 text-white font-semibold text-xs py-2 px-3 rounded-lg shadow-sm', html)

    def test_2_filter_button_placed_immediately_to_left_of_delete_range(self):
        """3: Filter button appears immediately to the left of Delete Range."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Ensure top Filter button is present with correct ID, onclick, styling
        self.assertIn('id="toggle-coupon-filter-btn"', html)
        self.assertIn('onclick="toggleCouponFilterPanel()"', html)
        self.assertIn('bg-teal-600 hover:bg-teal-700 active:bg-teal-800 text-white font-semibold text-xs py-2 px-3 rounded-lg shadow-sm', html)

        # Check order: toggle-coupon-filter-btn comes before toggle-coupon-delete-range-btn
        filter_pos = html.find('id="toggle-coupon-filter-btn"')
        delete_pos = html.find('id="toggle-coupon-delete-range-btn"')
        self.assertTrue(filter_pos > 0 and delete_pos > 0)
        self.assertTrue(filter_pos < delete_pos, "Filter button must be immediately to the left of Delete Range button")

    def test_3_delete_range_interface_fields_and_delete_action(self):
        """4 & 5: Delete Range interface contains ONLY From Date and To Date, action labelled Delete."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        # Panel container
        self.assertIn('id="coupon-delete-range-panel"', html)

        # From Date and To Date input fields
        self.assertIn('id="deleteStartDate"', html)
        self.assertIn('id="deleteEndDate"', html)

        # Submit button inside panel labelled Delete
        self.assertIn('id="submit-delete-range-btn"', html)
        self.assertIn('onclick="deleteCouponsByDateRange()"', html)
        self.assertIn('Delete', html)

        # Extract Delete Range panel slice and verify absence of search/status inside it
        dr_start = html.find('id="coupon-delete-range-panel"')
        dr_end = html.find('<!-- Coupon History Table', dr_start)
        dr_html = html[dr_start:dr_end]

        self.assertNotIn('deleteStatus', dr_html)
        self.assertNotIn('deleteSearch', dr_html)
        self.assertNotIn('type="text"', dr_html)
        self.assertNotIn('<select', dr_html)

    def test_4_filter_panel_controls(self):
        """6: Filter panel contains From Date, To Date, Status, Search, Filter action, and Reset action."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        # Panel container
        self.assertIn('id="coupon-filter-panel"', html)

        # Extract filter panel slice
        fp_start = html.find('id="coupon-filter-panel"')
        fp_end = html.find('id="coupon-delete-range-panel"')
        fp_html = html[fp_start:fp_end]

        self.assertIn('id="historyStartDate"', fp_html)
        self.assertIn('id="historyEndDate"', fp_html)
        self.assertIn('id="historyStatus"', fp_html)
        self.assertIn('id="historySearch"', fp_html)
        self.assertIn('id="apply-coupon-filter-btn"', fp_html)
        self.assertIn('id="reset-coupon-filter-btn"', fp_html)

        # Actions
        self.assertIn('onclick="filterCouponHistory()"', fp_html)
        self.assertIn('onclick="clearDateFilter()"', fp_html)

    def test_5_initial_visibility_both_panels_hidden(self):
        """Both filter panel and delete range panel start hidden by default."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        self.assertIn('id="coupon-filter-panel" class="hidden ', html)
        self.assertIn('id="coupon-delete-range-panel" class="hidden ', html)

    def test_6_javascript_independent_toggles_and_persistence(self):
        """Verify toggleCouponFilterPanel and toggleCouponDeleteRangePanel are separate, and persistence logic is defined."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        # Independent functions
        self.assertIn('function toggleCouponFilterPanel', html)
        self.assertIn('function toggleCouponDeleteRangePanel', html)

        # Storage keys and functions
        self.assertIn('coupon_history_filter_panel_open', html)
        self.assertIn('function isPageReload', html)
        self.assertIn('sessionStorage', html)

        # Navigation clearance & bfcache
        self.assertIn("targetUrl.pathname !== window.location.pathname", html)
        self.assertIn("window.addEventListener('pageshow'", html)
        self.assertIn("event.persisted", html)

    def test_7_delete_by_date_range_uses_new_inputs(self):
        """Verify deleteCouponsByDateRange reads deleteStartDate and deleteEndDate with confirmation."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        self.assertIn("document.getElementById('deleteStartDate')", html)
        self.assertIn("document.getElementById('deleteEndDate')", html)
        self.assertIn('/admin/delete_coupons_by_date_range', html)

    def test_8_dashboard_link_intact_on_other_admin_pages(self):
        """Dashboard navigation intact on other admin pages (e.g. settings, calendar)."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/settings')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('/admin/dashboard', html)

if __name__ == '__main__':
    unittest.main()
