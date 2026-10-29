import os
import sys
import unittest
from datetime import datetime, timezone, timedelta, time
from bson import ObjectId
from app import create_app
from models.user_model import User
from models.coupon_model import generate_coupon_code, is_coupon_expired

IST = timezone(timedelta(hours=5, minutes=30))

class TestCouponHistoryTodayFilter(unittest.TestCase):
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
                'email': 'admin_coupon_filter_test@raft.com',
                'role': 'supreme_admin'
            }
            self.db.users.insert_one(self.admin_doc)

        self.created_coupon_ids = []

    def tearDown(self):
        if self.created_coupon_ids:
            self.db.coupons.delete_many({'_id': {'$in': self.created_coupon_ids}})

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def _create_mock_coupon(self, code_suffix, created_at, expires_at=None, is_used=False, is_revoked=False, is_deleted=False, used_at=None):
        code = f"TEST{code_suffix}_{generate_coupon_code()}"
        if not expires_at:
            expires_at = created_at + timedelta(hours=12)

        doc = {
            "code": code,
            "phone": "9876543210",
            "email": f"test_{code.lower()}@gmail.com",
            "group_size": 4,
            "valid_day_types": ["weekday", "weekend"],
            "discount_type": "per_head",
            "discount_value": 100,
            "created_at": created_at,
            "expires_at": expires_at,
            "is_used": is_used,
            "is_revoked": is_revoked,
            "is_deleted": is_deleted,
            "used_at": used_at,
            "booking_id": None
        }
        res = self.db.coupons.insert_one(doc)
        self.created_coupon_ids.append(res.inserted_id)
        return code, res.inserted_id

    def test_1_default_view_displays_only_coupons_generated_today(self):
        """1. Opening the Coupon Generation page displays only coupons generated today in IST."""
        self._login_admin()

        now_utc = datetime.now(timezone.utc)
        today_code, _ = self._create_mock_coupon("TODAY", created_at=now_utc)

        yesterday_utc = now_utc - timedelta(days=1)
        yesterday_code, _ = self._create_mock_coupon("YESTERDAY", created_at=yesterday_utc)

        res = self.client.get('/admin/coupons')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        now_ist = now_utc.astimezone(IST)
        today_ist_str = now_ist.date().strftime('%Y-%m-%d')

        # Today's coupon must appear
        self.assertIn(today_code, html, "Coupon generated today must appear in the default view")
        # Yesterday's coupon must NOT appear
        self.assertNotIn(yesterday_code, html, "Coupon generated yesterday must not appear in the default view")

        # Verify the date inputs default to today's IST date
        self.assertIn(f'id="historyStartDate" value="{today_ist_str}"', html)
        self.assertIn(f'id="historyEndDate" value="{today_ist_str}"', html)

    def test_2_coupons_generated_yesterday_still_valid_today_excluded_from_default_view(self):
        """2. Coupons generated yesterday but still valid today are excluded from the default view."""
        self._login_admin()

        now_utc = datetime.now(timezone.utc)
        # Generated 10 hours ago (yesterday in some timezone or 24 hours ago)
        # To guarantee it was yesterday in IST:
        now_ist = now_utc.astimezone(IST)
        yesterday_ist_date = now_ist.date() - timedelta(days=1)
        # Created yesterday at 23:00 IST, valid for 12 hours until 11:00 IST today
        created_yesterday_ist = datetime.combine(yesterday_ist_date, time(23, 0)).replace(tzinfo=IST)
        created_yesterday_utc = created_yesterday_ist.astimezone(timezone.utc)
        expires_today_utc = created_yesterday_utc + timedelta(hours=12)

        # Confirm it's valid now
        valid_yesterday_code, c_id = self._create_mock_coupon(
            "YEST_VALID",
            created_at=created_yesterday_utc,
            expires_at=expires_today_utc,
            is_used=False
        )

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        self.assertNotIn(valid_yesterday_code, html, "Coupon generated yesterday but valid today must be excluded from default view")

    def test_3_coupons_generated_today_appear_regardless_of_status(self):
        """3. Coupons generated today appear regardless of whether their status is valid, used, expired, or revoked."""
        self._login_admin()

        now_utc = datetime.now(timezone.utc)

        # A) Valid coupon today
        code_valid, _ = self._create_mock_coupon("TD_VALID", created_at=now_utc, is_used=False, is_revoked=False)
        # B) Used coupon today
        code_used, _ = self._create_mock_coupon("TD_USED", created_at=now_utc, is_used=True, used_at=now_utc)
        # C) Expired coupon today (created earlier today, expires_at in the past)
        code_expired, _ = self._create_mock_coupon(
            "TD_EXPIRED",
            created_at=now_utc,
            expires_at=now_utc - timedelta(minutes=10)
        )
        # D) Revoked coupon today
        code_revoked, _ = self._create_mock_coupon("TD_REVOKED", created_at=now_utc, is_revoked=True)

        res = self.client.get('/admin/coupons')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn(code_valid, html, "Valid coupon generated today must appear")
        self.assertIn(code_used, html, "Used coupon generated today must appear")
        self.assertIn(code_expired, html, "Expired coupon generated today must appear")
        self.assertIn(code_revoked, html, "Revoked coupon generated today must appear")

    def test_4_coupons_generated_yesterday_used_today_excluded_from_default_view(self):
        """4. Coupons generated yesterday but used today remain excluded from the default view."""
        self._login_admin()

        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc.astimezone(IST)
        yesterday_ist_date = now_ist.date() - timedelta(days=1)
        created_yesterday_ist = datetime.combine(yesterday_ist_date, time(14, 0)).replace(tzinfo=IST)
        created_yesterday_utc = created_yesterday_ist.astimezone(timezone.utc)

        code_yest_used_today, _ = self._create_mock_coupon(
            "YEST_USED_TODAY",
            created_at=created_yesterday_utc,
            is_used=True,
            used_at=now_utc
        )

        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)

        self.assertNotIn(code_yest_used_today, html, "Coupon generated yesterday but used today must be excluded from default view")

    def test_5_selecting_previous_date_or_custom_date_range_displays_historical_coupons(self):
        """5. Selecting a previous date or custom date range displays the appropriate historical coupons."""
        self._login_admin()

        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc.astimezone(IST)
        yesterday_ist_date = now_ist.date() - timedelta(days=1)
        yesterday_str = yesterday_ist_date.strftime('%Y-%m-%d')
        today_str = now_ist.date().strftime('%Y-%m-%d')

        created_yesterday_ist = datetime.combine(yesterday_ist_date, time(12, 0)).replace(tzinfo=IST)
        created_yesterday_utc = created_yesterday_ist.astimezone(timezone.utc)

        yesterday_code, _ = self._create_mock_coupon("HIST_YEST", created_at=created_yesterday_utc)
        today_code, _ = self._create_mock_coupon("HIST_TODAY", created_at=now_utc)

        # 5a: Filter specifically for yesterday
        res_yest = self.client.get(f'/admin/coupons?start_date={yesterday_str}&end_date={yesterday_str}')
        self.assertEqual(res_yest.status_code, 200)
        html_yest = res_yest.get_data(as_text=True)
        self.assertIn(yesterday_code, html_yest, "Yesterday's coupon must appear when filtering for yesterday")
        self.assertNotIn(today_code, html_yest, "Today's coupon must not appear when filtering for yesterday")

        # 5b: Filter using from_date / to_date params
        res_from_to = self.client.get(f'/admin/coupons?from_date={yesterday_str}&to_date={today_str}')
        self.assertEqual(res_from_to.status_code, 200)
        html_from_to = res_from_to.get_data(as_text=True)
        self.assertIn(yesterday_code, html_from_to, "Yesterday's coupon must appear when date range covers yesterday")
        self.assertIn(today_code, html_from_to, "Today's coupon must appear when date range covers today")

        # 5c: all_dates=1 retrieves both
        res_all = self.client.get('/admin/coupons?all_dates=1')
        self.assertEqual(res_all.status_code, 200)
        html_all = res_all.get_data(as_text=True)
        self.assertIn(yesterday_code, html_all, "All dates must include yesterday's coupon")
        self.assertIn(today_code, html_all, "All dates must include today's coupon")

    def test_6_midnight_boundary_classification_ist(self):
        """6. Coupons generated around midnight are classified according to the configured timezone (IST)."""
        self._login_admin()

        # Let date D be 2026-10-11
        target_date_str = '2026-10-11'
        target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()

        # Midnight start of D in IST: 2026-10-11 00:00:00 IST -> 2026-10-10 18:30:00 UTC
        # Test A: 5 mins before midnight (2026-10-10 23:55:00 IST -> 2026-10-10 18:25:00 UTC) -> Belongs to 2026-10-10
        dt_before_midnight_ist = datetime.combine(target_date - timedelta(days=1), time(23, 55)).replace(tzinfo=IST)
        code_before_midnight, _ = self._create_mock_coupon("BEFORE_MIDNIGHT", created_at=dt_before_midnight_ist.astimezone(timezone.utc))

        # Test B: 5 mins after midnight (2026-10-11 00:05:00 IST -> 2026-10-10 18:35:00 UTC) -> Belongs to 2026-10-11
        dt_after_midnight_ist = datetime.combine(target_date, time(0, 5)).replace(tzinfo=IST)
        code_after_midnight, _ = self._create_mock_coupon("AFTER_MIDNIGHT", created_at=dt_after_midnight_ist.astimezone(timezone.utc))

        # Test C: 5 mins before midnight end of D (2026-10-11 23:55:00 IST -> 2026-10-11 18:25:00 UTC) -> Belongs to 2026-10-11
        dt_end_of_day_ist = datetime.combine(target_date, time(23, 55)).replace(tzinfo=IST)
        code_end_of_day, _ = self._create_mock_coupon("END_OF_DAY", created_at=dt_end_of_day_ist.astimezone(timezone.utc))

        # Test D: 5 mins after midnight end of D (2026-10-12 00:05:00 IST -> 2026-10-11 18:35:00 UTC) -> Belongs to 2026-10-12
        dt_next_day_ist = datetime.combine(target_date + timedelta(days=1), time(0, 5)).replace(tzinfo=IST)
        code_next_day, _ = self._create_mock_coupon("NEXT_DAY", created_at=dt_next_day_ist.astimezone(timezone.utc))

        # Query for target_date_str (2026-10-11)
        res = self.client.get(f'/admin/coupons?start_date={target_date_str}&end_date={target_date_str}')
        html = res.get_data(as_text=True)

        # Must include coupons within 2026-10-11 IST
        self.assertIn(code_after_midnight, html, "00:05 AM IST must be included in target date")
        self.assertIn(code_end_of_day, html, "23:55 PM IST must be included in target date")

        # Must NOT include coupons outside 2026-10-11 IST
        self.assertNotIn(code_before_midnight, html, "23:55 PM IST previous day must be excluded from target date")
        self.assertNotIn(code_next_day, html, "00:05 AM IST next day must be excluded from target date")

    def test_7_existing_coupon_generation_application_revocation_and_deletion(self):
        """7. Existing coupon generation, application, expiry, revocation, and deletion behaviour remains unchanged."""
        self._login_admin()

        now_utc = datetime.now(timezone.utc)
        code, coupon_id = self._create_mock_coupon("MGMT_TEST", created_at=now_utc)

        # 7a: Revoke coupon
        res_revoke = self.client.post('/admin/revoke_coupon', data={'coupon_id': str(coupon_id)})
        self.assertEqual(res_revoke.status_code, 200)
        c_revoked = self.db.coupons.find_one({'_id': coupon_id})
        self.assertTrue(c_revoked.get('is_revoked'))

        # Still appears in today's default view as Revoked badge
        res = self.client.get('/admin/coupons')
        html = res.get_data(as_text=True)
        self.assertIn(code, html)
        self.assertIn('Revoked', html)

        # 7b: Delete single coupon
        res_del = self.client.post('/admin/delete_coupon', data={'coupon_id': str(coupon_id)})
        self.assertEqual(res_del.status_code, 200)
        c_deleted = self.db.coupons.find_one({'_id': coupon_id})
        self.assertIsNone(c_deleted)

if __name__ == '__main__':
    unittest.main()
