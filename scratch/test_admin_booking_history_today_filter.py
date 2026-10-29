import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from bson import ObjectId

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from routes.admin_routes import IST

class TestAdminBookingHistoryTodayFilter(unittest.TestCase):
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
                'email': 'admin_test_today_filter@raft.com',
                'role': 'supreme_admin'
            })

    def setUp(self):
        # Clean up test-tagged admin bookings before each test
        self.db.bookings.delete_many({'test_tag': 'today_filter_test'})

    def tearDown(self):
        # Clean up test bookings
        self.db.bookings.delete_many({'test_tag': 'today_filter_test'})

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def _create_booking(self, created_at, scheduled_date, payment_mode='Cash', slot='7:00am', name='Test User'):
        booking = {
            'user_id': 'test@user.com',
            'user_name': name,
            'name': name,
            'email': 'test@user.com',
            'phone': '9876543210',
            'location': 'Kozhikode',
            'date': scheduled_date,
            'slot': slot,
            'group_size': 4,
            'total_amount': 4000.0,
            'advance_amount': 1000.0,
            'balance_amount': 3000.0,
            'payment_mode': payment_mode,
            'status': 'Confirmed',
            'payment_status': 'Paid',
            'is_admin_booking': True,
            'created_at': created_at,
            'created_by': 'Admin',
            'test_tag': 'today_filter_test'
        }
        return self.db.bookings.insert_one(booking).inserted_id

    def test_1_default_view_shows_today_bookings_only(self):
        """Initial GET /admin/booking shows only bookings created today in IST."""
        self._login()
        now_ist = datetime.now(timezone.utc).astimezone(IST)
        today_str = now_ist.date().strftime('%Y-%m-%d')
        yesterday_str = (now_ist.date() - timedelta(days=1)).strftime('%Y-%m-%d')

        # Booking A: Created TODAY in IST, scheduled for today
        created_today = datetime.now(timezone.utc)
        id_today = self._create_booking(created_today, today_str, name='Today Booking')

        # Booking B: Created YESTERDAY in IST, scheduled for today
        created_yesterday = (datetime.now(timezone.utc) - timedelta(days=1))
        id_yesterday = self._create_booking(created_yesterday, today_str, name='Yesterday Booking')

        res = self.client.get('/admin/booking')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Must include booking created today
        self.assertIn('Today Booking', html)
        # Must exclude booking created yesterday, even though its scheduled date is today!
        self.assertNotIn('Yesterday Booking', html)
        # Input fields must default to today_str
        self.assertIn(f'value="{today_str}"', html)

    def test_2_today_booking_scheduled_for_future_is_included(self):
        """A booking created today for a rafting session scheduled next week must appear in today's view."""
        self._login()
        now_ist = datetime.now(timezone.utc).astimezone(IST)
        future_date_str = (now_ist.date() + timedelta(days=7)).strftime('%Y-%m-%d')

        created_today = datetime.now(timezone.utc)
        self._create_booking(created_today, future_date_str, name='Future Scheduled Rafting')

        res = self.client.get('/admin/booking')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('Future Scheduled Rafting', html)
        self.assertIn(future_date_str, html)

    def test_3_older_booking_scheduled_for_today_is_excluded(self):
        """A booking created yesterday for a session scheduled today must NOT appear in default view."""
        self._login()
        now_ist = datetime.now(timezone.utc).astimezone(IST)
        today_str = now_ist.date().strftime('%Y-%m-%d')

        # Created 2 days ago
        created_2_days_ago = datetime.now(timezone.utc) - timedelta(days=2)
        self._create_booking(created_2_days_ago, today_str, name='Old Booking For Today')

        res = self.client.get('/admin/booking')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertNotIn('Old Booking For Today', html)

    def test_4_midnight_boundary_in_ist(self):
        """Bookings created around midnight are properly classified using IST (UTC+05:30)."""
        self._login()
        now_ist = datetime.now(timezone.utc).astimezone(IST)
        today_date = now_ist.date()
        today_str = today_date.strftime('%Y-%m-%d')
        yesterday_date = today_date - timedelta(days=1)
        yesterday_str = yesterday_date.strftime('%Y-%m-%d')

        # 23:59:00 IST of yesterday -> UTC is 18:29:00 of 2 days ago / yesterday
        yesterday_late_ist = datetime(yesterday_date.year, yesterday_date.month, yesterday_date.day, 23, 59, 0, tzinfo=IST)
        created_yesterday_late_utc = yesterday_late_ist.astimezone(timezone.utc)
        self._create_booking(created_yesterday_late_utc, today_str, name='Late Night Yesterday Booking')

        # 00:01:00 IST of today -> UTC is 18:31:00 of yesterday
        today_early_ist = datetime(today_date.year, today_date.month, today_date.day, 0, 1, 0, tzinfo=IST)
        created_today_early_utc = today_early_ist.astimezone(timezone.utc)
        self._create_booking(created_today_early_utc, today_str, name='Early Morning Today Booking')

        res = self.client.get('/admin/booking')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Early morning today (00:01 IST) MUST be included
        self.assertIn('Early Morning Today Booking', html)
        # Late night yesterday (23:59 IST) MUST be excluded
        self.assertNotIn('Late Night Yesterday Booking', html)

    def test_5_custom_date_range_selection(self):
        """Admin can specify custom date range to see historical records."""
        self._login()
        # Create a booking on 2026-05-10
        past_created_ist = datetime(2026, 5, 10, 14, 0, 0, tzinfo=IST)
        past_created_utc = past_created_ist.astimezone(timezone.utc)
        self._create_booking(past_created_utc, '2026-05-15', name='May 2026 Historical Booking')

        # Query with date range covering 2026-05-10
        res = self.client.get('/admin/booking?from_date=2026-05-01&to_date=2026-05-20&filter_panel=open')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('May 2026 Historical Booking', html)
        self.assertIn('value="2026-05-01"', html)
        self.assertIn('value="2026-05-20"', html)

    def test_6_clear_date_filter_displays_all_records(self):
        """Admin explicitly clearing date filters (submitting empty from_date and to_date) sees all records."""
        self._login()
        now_ist = datetime.now(timezone.utc).astimezone(IST)
        past_created = datetime.now(timezone.utc) - timedelta(days=10)
        today_created = datetime.now(timezone.utc)

        self._create_booking(past_created, '2026-10-01', name='Past Booking In All Records')
        self._create_booking(today_created, '2026-10-15', name='Today Booking In All Records')

        res = self.client.get('/admin/booking?from_date=&to_date=&slot=All&payment_mode=All')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn('Past Booking In All Records', html)
        self.assertIn('Today Booking In All Records', html)

    def test_7_slot_and_payment_mode_filters(self):
        """Filtering by slot and payment mode works properly along with date filtering."""
        self._login()
        now_ist = datetime.now(timezone.utc).astimezone(IST)
        today_str = now_ist.date().strftime('%Y-%m-%d')

        created_today = datetime.now(timezone.utc)
        self._create_booking(created_today, today_str, payment_mode='Cash', slot='7:00am', name='Morning Cash User')
        self._create_booking(created_today, today_str, payment_mode='Bank Transfer', slot='11:00am', name='Noon Transfer User')

        # Filter by Morning slot
        res_slot = self.client.get(f'/admin/booking?from_date={today_str}&to_date={today_str}&slot=7:00am')
        html_slot = res_slot.get_data(as_text=True)
        self.assertIn('Morning Cash User', html_slot)
        self.assertNotIn('Noon Transfer User', html_slot)

        # Filter by Bank Transfer
        res_mode = self.client.get(f'/admin/booking?from_date={today_str}&to_date={today_str}&payment_mode=Bank Transfer')
        html_mode = res_mode.get_data(as_text=True)
        self.assertNotIn('Morning Cash User', html_mode)
        self.assertIn('Noon Transfer User', html_mode)

    def test_8_ui_labels_renamed_to_raft_booking(self):
        """Visible labels and headings in customer booking page are renamed to 'Raft Booking'."""
        self._login()
        res = self.client.get('/admin/booking')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Heading and subheader
        self.assertIn('Raft Booking', html)
        self.assertIn('Raft Booking History', html)
        # Old labels should not appear as headings
        self.assertNotIn('Custom Booking', html)
        self.assertNotIn('Customer Booking History', html)
        # Sidebar link in admin_base.html
        self.assertIn('<span>Raft Booking</span>', html)
        self.assertNotIn('<span>Customer Booking</span>', html)

if __name__ == '__main__':
    unittest.main()
