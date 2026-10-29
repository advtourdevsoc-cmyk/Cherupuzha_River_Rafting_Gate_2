import os
import sys
import unittest
from bson import ObjectId
from app import create_app
from flask import render_template
from flask_login import login_user
from models.user_model import User

class TestAdminBookingHistoryFilter(unittest.TestCase):
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
                'email': 'admin_test_filter_btn@raft.com',
                'role': 'supreme_admin'
            }
            self.db.users.insert_one(self.admin_doc)

    def test_1_dashboard_button_removed_and_replaced_with_filter_button(self):
        """Verify '← Dashboard' is removed from Customer Booking History section and replaced by 'Filter' button."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/booking')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Ensure ← Dashboard is NOT in the Customer Booking History card header
        self.assertNotIn('← Dashboard', html)

        # Ensure top Filter button is present with correct ID, onclick, styling and label
        self.assertIn('id="toggle-history-filter-btn"', html)
        self.assertIn('onclick="toggleHistoryFilterPanel()"', html)
        self.assertIn('Filter', html)
        self.assertIn('bg-teal-600 hover:bg-teal-700 text-white text-xs font-bold py-2 px-3 rounded-lg', html)

    def test_2_filter_panel_and_inner_apply_button(self):
        """Verify the filter panel ID, hidden filter_panel parameter, and that the inner button is renamed to 'Apply'."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/booking')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Panel ID exists
        self.assertIn('id="history-filter-panel"', html)

        # Hidden input for Apply form submission
        self.assertIn('name="filter_panel" value="open"', html)

        # Inner button is renamed to Apply
        self.assertIn('id="apply-history-filter-btn"', html)
        self.assertIn('🔍 Apply', html)
        # Inner button preserves styling
        self.assertIn('flex-1 bg-teal-600 hover:bg-teal-700 text-white text-xs font-bold py-2 px-3 rounded-lg transition-colors', html)

    def test_3_panel_hidden_by_default_on_initial_visit_and_return(self):
        """When visiting /admin/booking as a new visit or return from another page, panel is initially hidden."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/booking')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Check that history-filter-panel starts with hidden
        self.assertIn('id="history-filter-panel" class="hidden ', html)

    def test_4_panel_open_when_apply_submits_filter_panel_param(self):
        """When filters are applied via Apply button, filter_panel=open keeps the panel expanded."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/booking?from_date=2026-10-01&to_date=2026-10-15&filter_panel=open')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Since filter_panel=open is present, panel is open (NOT hidden)
        self.assertIn('id="history-filter-panel" class="p-4 bg-gray-50 rounded-xl border border-gray-200', html)
        self.assertNotIn('id="history-filter-panel" class="hidden ', html)

        # And filter values remain populated
        self.assertIn('value="2026-10-01"', html)
        self.assertIn('value="2026-10-15"', html)

    def test_5_panel_hidden_when_returning_after_leaving_page(self):
        """When navigating to Create Booking or any other page and returning to /admin/booking, panel starts hidden."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # 1. Simulate applying filters (panel is open)
        res_filtered = self.client.get('/admin/booking?from_date=2026-10-01&filter_panel=open')
        self.assertNotIn('id="history-filter-panel" class="hidden ', res_filtered.get_data(as_text=True))

        # 2. Simulate navigating to Create Booking form
        res_create = self.client.get('/admin/booking/create')
        self.assertEqual(res_create.status_code, 200)

        # 3. Simulate returning to Customer Booking page
        res_return = self.client.get('/admin/booking')
        html_return = res_return.get_data(as_text=True)
        # Must be HIDDEN!
        self.assertIn('id="history-filter-panel" class="hidden ', html_return)

    def test_6_no_session_storage_used_and_back_forward_cache_handled(self):
        """Verify sessionStorage is not used and bfcache/pageshow/pagehide listeners are implemented."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/booking')
        html = res.get_data(as_text=True)

        # Confirm no sessionStorage in admin_booking.html
        self.assertNotIn('sessionStorage', html)
        self.assertNotIn('localStorage', html)

        # Confirm toggleHistoryFilterPanel implementation
        self.assertIn('function toggleHistoryFilterPanel', html)

        # Confirm pageshow and pagehide listeners to handle bfcache and navigation resets
        self.assertIn("window.addEventListener('pageshow'", html)
        self.assertIn("window.addEventListener('pagehide'", html)
        self.assertIn("event.persisted", html)

    def test_7_other_dashboard_links_unaffected(self):
        """Verify dashboard link in other pages (e.g. Coupon Generation) is unaffected."""
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.get('/admin/settings')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # In other admin pages, dashboard link remains intact
        self.assertIn('/admin/dashboard', html)

if __name__ == '__main__':
    unittest.main()
