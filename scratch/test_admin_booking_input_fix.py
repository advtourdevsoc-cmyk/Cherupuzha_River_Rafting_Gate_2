import os
import sys
import unittest
from datetime import date, timedelta
from bson import ObjectId

# Ensure workspace root in path
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from app import create_app

class TestAdminBookingInputFix(unittest.TestCase):
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

        # Ensure test rafts exist
        if self.db.rafts.count_documents({}) == 0:
            for i in range(1, 6):
                self.db.rafts.insert_one({
                    'raft_number': f'R{i}',
                    'capacity': 6,
                    'status': 'Active'
                })

        # Find or create supreme admin user
        existing_admin = self.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            self.admin_id = existing_admin['_id']
            self.admin_doc = existing_admin
        else:
            self.admin_id = ObjectId()
            self.admin_doc = {
                '_id': self.admin_id,
                'name': 'Test Admin',
                'email': 'admin_test_fix@raft.com',
                'role': 'supreme_admin'
            }
            self.db.users.insert_one(self.admin_doc)

    def tearDown(self):
        self.db.bookings.delete_many({'phone': {'$in': ['9876543210', '9876543211']}})
        self.db.rafts.delete_many({'day': {'$in': [
            (date.today() + timedelta(days=2)).isoformat(),
            (date.today() + timedelta(days=3)).isoformat()
        ]}})

    def test_1_template_static_checks(self):
        """
        Verify template code fixes:
        - No input or change listener on custom_advance_amount triggering recalculatePricing
        - No input or change listener on per_person_discount triggering recalculatePricing
        - recalculatePricing does NOT overwrite customAdvInput.value or clamp customAdvInput.max
        - pricingReqId is present for sequencing async responses
        - booking_date, slot-select, and group_size_input continue triggering recalculatePricing
        - checkPhoneCoupon does NOT call recalculatePricing on phone/email keystrokes
        """
        template_path = os.path.join(WORKSPACE_ROOT, 'templates', 'admin_booking_form.html')
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 1. Ensure customAdvInput is not overwritten or clamped in recalculatePricing
        self.assertNotIn("customAdvInput.value = data.total_amount", content,
            "recalculatePricing must not overwrite customAdvInput.value")
        self.assertNotIn("customAdvInput.max = data.total_amount", content,
            "recalculatePricing must not clamp customAdvInput.max")

        # 2. Ensure custom_advance_amount has no recalculatePricing event listeners
        self.assertNotIn("customAdv.addEventListener('input', recalculatePricing)", content,
            "custom_advance_amount must not trigger recalculatePricing on input")
        self.assertNotIn("customAdv.addEventListener('change', recalculatePricing)", content,
            "custom_advance_amount must not trigger recalculatePricing on change")

        # 3. Ensure per_person_discount has no recalculatePricing event listeners
        self.assertNotIn("perPersonDisc.addEventListener('input', recalculatePricing)", content,
            "per_person_discount must not trigger recalculatePricing on input")
        self.assertNotIn("perPersonDisc.addEventListener('change', recalculatePricing)", content,
            "per_person_discount must not trigger recalculatePricing on change")

        # 4. Ensure pricingReqId sequence tracking exists
        self.assertIn("pricingReqId", content,
            "pricingReqId must be defined for async request sequencing")
        self.assertIn("if (currentReqId !== pricingReqId) return;", content,
            "Stale asynchronous pricing responses must be discarded")

        # 5. Ensure allocation trigger fields (booking_date, slot, group_size) continue to trigger recalculatePricing
        self.assertIn("updateSlotAvailability(dateStr);", content)
        self.assertIn("recalculatePricing();", content)
        self.assertIn("groupSizeInput.addEventListener('input', function ()", content)

        # 6. Ensure checkPhoneCoupon does not call recalculatePricing
        # In checkPhoneCoupon body, recalculatePricing should not be called at the end
        self.assertNotIn("}\n      recalculatePricing();\n    }\n\n    if (phoneInput)", content)

    def test_2_pricing_calculation_endpoint(self):
        """Test /admin/calculate_admin_booking_pricing endpoint."""
        today_str = date.today().isoformat()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        res = self.client.post('/admin/calculate_admin_booking_pricing', json={
            'date': today_str,
            'slot': '7:00am',
            'group_size': 4,
            'custom_advance': '1000'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['final_advance'], 1000.0)
        self.assertIn('available_capacity', data)
        self.assertIn('has_small_group_capacity', data)

    def test_3_admin_form_get_and_post_booking(self):
        """Test GET form page and POST creation with custom advance amount."""
        today_str = (date.today() + timedelta(days=2)).isoformat()
        self.db.bookings.delete_many({'date': today_str})
        self.db.rafts.delete_many({'day': today_str})
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # GET form page
        res_get = self.client.get('/admin/booking/create')
        self.assertEqual(res_get.status_code, 200)
        get_html = res_get.get_data(as_text=True)
        self.assertIn('custom_advance_amount', get_html)

        # POST booking with advance 1200
        post_data = {
            'name': 'Advance Amount Tester',
            'phone': '9876543210',
            'email': 'advancetester@gmail.com',
            'location': 'Kannur',
            'booking_date': today_str,
            'slot': '7:00am',
            'group_size': '4',
            'custom_advance_amount': '1200',
            'payment_mode': 'Cash',
            'admin_notes': 'Testing advance retention'
        }
        res_post = self.client.post('/admin/booking/create', data=post_data, follow_redirects=False)
        self.assertEqual(res_post.status_code, 302)
        # 1. Assert redirect directly to /admin/booking (NOT /admin/booking/success/)
        self.assertNotIn('/admin/booking/success/', res_post.headers['Location'])
        self.assertTrue(res_post.headers['Location'].endswith('/admin/booking'))

        # Verify DB booking creation
        booking = self.db.bookings.find_one({'phone': '9876543210', 'date': today_str}, sort=[('created_at', -1)])
        self.assertIsNotNone(booking)
        self.assertEqual(booking['advance_amount'], 1200.0)
        self.assertEqual(booking['payment_mode'], 'Cash')

        # 2. Follow redirect to /admin/booking and verify temporary toast notification
        res_redirect = self.client.get('/admin/booking')
        self.assertEqual(res_redirect.status_code, 200)
        redirect_html = res_redirect.get_data(as_text=True)
        self.assertIn('id="booking-success-toast"', redirect_html)
        self.assertIn('Booking Successful!', redirect_html)
        self.assertIn(str(booking['_id']), redirect_html)
        self.assertIn('Advance Amount Tester', redirect_html)
        self.assertIn(today_str, redirect_html)
        self.assertIn('7:00am', redirect_html)
        self.assertIn('4 participants', redirect_html)
        self.assertIn('setTimeout(function ()', redirect_html)

        # 3. Subsequent visit / refresh: notification must NOT appear again
        res_refresh = self.client.get('/admin/booking')
        self.assertEqual(res_refresh.status_code, 200)
        refresh_html = res_refresh.get_data(as_text=True)
        self.assertNotIn('id="booking-success-toast"', refresh_html)

    def test_4_form_validation_error_preserves_advance_amount(self):
        """Test that validation error rerenders form with custom_advance_amount preserved."""
        today_str = (date.today() + timedelta(days=3)).isoformat()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

        # Missing name causes validation error
        post_data = {
            'name': '',
            'phone': '9876543211',
            'email': 'noname@gmail.com',
            'location': 'Kannur',
            'booking_date': today_str,
            'slot': '7:00am',
            'group_size': '4',
            'custom_advance_amount': '1500',
            'payment_mode': 'Cash',
            'admin_notes': ''
        }
        res_post = self.client.post('/admin/booking/create', data=post_data, follow_redirects=False)
        self.assertEqual(res_post.status_code, 200)
        html = res_post.get_data(as_text=True)
        # Verify custom_advance_amount value='1500' is rendered in input
        self.assertIn('value="1500"', html, "Form must retain entered advance amount on validation failure")

if __name__ == '__main__':
    unittest.main()
