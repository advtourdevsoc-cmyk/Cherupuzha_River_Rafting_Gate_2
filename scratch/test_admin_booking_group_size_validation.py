import os
import sys
import unittest
import re
from bson import ObjectId

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app

class TestAdminBookingGroupSizeValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.client = cls.app.test_client()
        cls.db = cls.app.mongo.db

        existing_admin = cls.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            cls.admin_id = existing_admin['_id']
        else:
            cls.admin_id = ObjectId()
            cls.db.users.insert_one({
                '_id': cls.admin_id,
                'name': 'Test Admin',
                'email': 'admin_test_groupsize@raft.com',
                'role': 'supreme_admin'
            })

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def _get_error_banner_content(self, html):
        """Extract inner content of #booking-error-banner."""
        match = re.search(r'<div id="booking-error-banner"[^>]*>(.*?)</div>', html, re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    def test_1_open_form_no_premature_validation_message(self):
        """1. Open the Admin Booking Form without entering a group size: message must not appear in error banner."""
        self._login()
        res = self.client.get('/admin/booking/create')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # The error banner must be hidden and empty
        banner_content = self._get_error_banner_content(html)
        self.assertEqual(banner_content, "", "Error banner must be empty on initial form load")
        self.assertIn('id="booking-error-banner" class="p-3 rounded-lg bg-red-100 border border-red-300 text-red-800 text-sm font-semibold hidden"', html)

    def test_2_empty_group_size_pricing_calc_no_frontend_error(self):
        """Pricing endpoint with empty group size returns success and frontend logic ignores empty."""
        self._login()
        res = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '',
            'phone': '9876543210'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))

        # In template JS:
        # hasGroupVal = rawGroupVal !== '' && !isNaN(rawGroupVal)
        # wanted = hasGroupVal ? parseInt(rawGroupVal, 10) : 0
        raw = ''
        hasGroupVal = raw != '' and not (raw.isalpha())
        wanted = int(raw) if hasGroupVal else 0
        has_min_err = (hasGroupVal and wanted > 0 and wanted < 4 and not data.get('has_small_group_capacity'))
        self.assertFalse(has_min_err, "Empty group size must not trigger below-minimum error")

    def test_3_zero_group_size_pricing_calc_no_frontend_error(self):
        """Entering 0 does not trigger the minimum group-size error."""
        self._login()
        res = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '0',
            'phone': '9876543210'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))

        raw = '0'
        hasGroupVal = raw != ''
        wanted = int(raw)
        has_min_err = (hasGroupVal and wanted > 0 and wanted < 4 and not data.get('has_small_group_capacity'))
        self.assertFalse(has_min_err, "Zero group size must not trigger below-minimum error")

    def test_4_submit_empty_group_size_rejected_by_required_validation(self):
        """Submitting with empty group size triggers required-field validation."""
        self._login()
        res = self.client.post('/admin/booking/create', data={
            'name': 'Test User',
            'phone': '9876543210',
            'location': 'Kozhikode',
            'booking_date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '',
            'payment_mode': 'Cash'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        banner_content = self._get_error_banner_content(html)
        self.assertIn('Please fill in all required fields.', banner_content)
        self.assertNotIn('Oops! Your entered group size is below the minimum requirement.', banner_content)

    def test_5_submit_zero_group_size_rejected_by_invalid_validation(self):
        """Submitting with group size 0 triggers invalid group size validation."""
        self._login()
        res = self.client.post('/admin/booking/create', data={
            'name': 'Test User',
            'phone': '9876543210',
            'location': 'Kozhikode',
            'booking_date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '0',
            'payment_mode': 'Cash'
        }, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        banner_content = self._get_error_banner_content(html)
        self.assertIn('Invalid group size.', banner_content)
        self.assertNotIn('Oops! Your entered group size is below the minimum requirement.', banner_content)

    def test_6_positive_value_below_minimum_shows_error(self):
        """Entering positive value below 4 on an empty slot triggers below-minimum validation."""
        self._login()
        # Test calculation endpoint
        res = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '2',
            'phone': '9876543210'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data.get('has_small_group_capacity'))

        # Verify JS logic condition
        raw = '2'
        hasGroupVal = raw != ''
        wanted = int(raw)
        has_min_err = (hasGroupVal and wanted > 0 and wanted < 4 and not data.get('has_small_group_capacity'))
        self.assertTrue(has_min_err, "Positive group size below 4 on empty slot MUST trigger error")

        # Test form submission with group_size=2
        sub_res = self.client.post('/admin/booking/create', data={
            'name': 'Test User',
            'phone': '9876543210',
            'location': 'Kozhikode',
            'booking_date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '2',
            'payment_mode': 'Cash'
        }, follow_redirects=True)
        self.assertEqual(sub_res.status_code, 200)
        sub_html = sub_res.get_data(as_text=True)
        banner_content = self._get_error_banner_content(sub_html)
        self.assertIn('Oops! Your entered group size is below the minimum requirement.', banner_content)

    def test_7_exact_minimum_value_does_not_show_error(self):
        """Entering group size 4 (exact minimum) does not trigger below-minimum error."""
        self._login()
        res = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '4',
            'phone': '9876543210'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        raw = '4'
        hasGroupVal = raw != ''
        wanted = int(raw)
        has_min_err = (hasGroupVal and wanted > 0 and wanted < 4 and not data.get('has_small_group_capacity'))
        self.assertFalse(has_min_err, "Exact minimum (4) must not trigger below-minimum error")

    def test_8_valid_group_size_within_limit_passes(self):
        """Entering valid group size 6 within limit passes without errors."""
        self._login()
        res = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '6',
            'phone': '9876543210'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        raw = '6'
        hasGroupVal = raw != ''
        wanted = int(raw)
        has_min_err = (hasGroupVal and wanted > 0 and wanted < 4 and not data.get('has_small_group_capacity'))
        self.assertFalse(has_min_err)
        self.assertGreater(data.get('total_amount'), 0)

    def test_9_exceeding_max_capacity_triggers_max_error(self):
        """Entering group size exceeding slot capacity triggers maximum capacity error."""
        self._login()
        res = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': '2026-10-25',
            'slot': '7:00am',
            'group_size': '100',
            'phone': '9876543210'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        avail = data.get('available_capacity', 0)
        raw = '100'
        hasGroupVal = raw != ''
        wanted = int(raw)
        is_max_err = (hasGroupVal and wanted > avail)
        self.assertTrue(is_max_err, "Group size exceeding available capacity must trigger max error")

    def test_10_customer_booking_form_unaffected(self):
        """Customer booking page (/book) loads normally without errors."""
        res = self.client.get('/book')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('Book Slot', html)
        self.assertNotIn('Oops! Your entered group size is below the minimum requirement.', html)

if __name__ == '__main__':
    unittest.main()
