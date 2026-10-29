import unittest
from bson import ObjectId
import datetime
from unittest.mock import patch
from pymongo.collection import Collection
from app import create_app
from models.booking_model import create_booking, update_booking_status
from models.payment_model import insert_payment

class TestAdminBookingDateRangeDeletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.db = cls.app.mongo.db

        # Configure system settings start_date and end_date to cover all test dates
        cls.db.settings.update_one(
            {'_id': 'system_settings'},
            {'$set': {
                'start_date': '2026-10-01',
                'end_date': '2026-10-31',
                'rafts_per_slot': 5,
                'capacity': 6,
                'time_slots': ['7:00am', '10:00am', '1:00pm', '3:30pm']
            }},
            upsert=True
        )

        existing_admin = cls.db.users.find_one({'role': 'supreme_admin'})
        if existing_admin:
            cls.admin_id = existing_admin['_id']
            cls.admin_doc = existing_admin
        else:
            cls.admin_id = ObjectId()
            cls.admin_doc = {
                '_id': cls.admin_id,
                'name': 'Test Supreme Admin',
                'email': 'supreme_test_delete@raft.com',
                'role': 'supreme_admin'
            }
            cls.db.users.insert_one(cls.admin_doc)

    def setUp(self):
        self.app = self.__class__.app
        self.client = self.app.test_client()
        self.db = self.__class__.db
        self.admin_id = self.__class__.admin_id
        self.created_booking_ids = []
        self.created_raft_keys = []

    def tearDown(self):
        if self.created_booking_ids:
            self.db.bookings.delete_many({'_id': {'$in': self.created_booking_ids}})
            self.db.payments.delete_many({'booking_id': {'$in': [str(bid) for bid in self.created_booking_ids] + self.created_booking_ids}})
        if self.created_raft_keys:
            for day, slot, rid in self.created_raft_keys:
                self.db.rafts.delete_one({'day': day, 'slot': slot, 'raft_id': rid})

    def _login_admin(self):
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.admin_id)
            sess['_fresh'] = True

    def _clean_dates(self, dates):
        self.db.bookings.delete_many({'date': {'$in': dates}})
        self.db.rafts.delete_many({'day': {'$in': dates}})

    def _create_customer_booking(self, date, status='Confirmed', slot='7:00am', group_size=4, raft_allocations=None):
        booking_details = {
            'name': f'Customer {date}',
            'email': f'cust_{date}@test.com',
            'phone': '9876543210',
            'location': 'Cherupuzha',
            'date': date,
            'slot': slot,
            'group_size': group_size,
            'amount_per_person': 700.0,
            'total_amount': 700.0 * group_size,
            'advance_amount': 700.0,
            'balance_amount': 700.0 * group_size - 700.0,
            'raft_allocations': raft_allocations or [],
            'is_admin_booking': False
        }
        b_id = create_booking(
            self.db,
            user_id=f'cust_{date}@test.com',
            booking_details=booking_details,
            amount=700.0 * group_size,
            currency='INR',
            status=status,
            payment_status='Paid',
            allow_payment=True,
            razorpay_order_id=f'ORDER_{date}_{ObjectId()}'
        )
        self.created_booking_ids.append(b_id)
        if raft_allocations:
            update_booking_status(
                self.db,
                b_id,
                status=status,
                raft_allocations=raft_allocations,
                extra_updates={'is_admin_booking': False}
            )
            for rid in raft_allocations:
                self.db.rafts.update_one(
                    {'day': date, 'slot': slot, 'raft_id': rid},
                    {'$set': {'occupancy': group_size, 'capacity': 6, 'is_special': False}},
                    upsert=True
                )
                self.created_raft_keys.append((date, slot, rid))

        insert_payment(
            self.db,
            booking_id=str(b_id),
            order_id=f'ORD_{b_id}',
            payment_id=f'PAY_{b_id}',
            signature='sig',
            amount=700.0,
            status='paid',
            raw_response={'test': True}
        )
        return b_id

    def _create_admin_booking(self, date, status='Confirmed', slot='7:00am', group_size=4, raft_allocations=None):
        booking_details = {
            'name': f'Admin Guest {date}',
            'email': f'adminguest_{date}@test.com',
            'phone': '9123456789',
            'location': 'Kannur',
            'date': date,
            'slot': slot,
            'group_size': group_size,
            'amount_per_person': 700.0,
            'total_amount': 700.0 * group_size,
            'advance_amount': 700.0,
            'balance_amount': 700.0 * group_size - 700.0,
            'raft_allocations': raft_allocations or [],
            'is_admin_booking': True,
            'payment_mode': 'Cash'
        }
        b_id = create_booking(
            self.db,
            user_id=f'adminguest_{date}@test.com',
            booking_details=booking_details,
            amount=700.0 * group_size,
            currency='INR',
            status=status,
            payment_status='Paid',
            allow_payment=True,
            razorpay_order_id=f'ADMIN_ORDER_{date}_{ObjectId()}'
        )
        self.created_booking_ids.append(b_id)
        update_booking_status(
            self.db,
            b_id,
            status=status,
            raft_allocations=raft_allocations or [],
            extra_updates={'is_admin_booking': True, 'payment_mode': 'Cash'}
        )
        if raft_allocations:
            for rid in raft_allocations:
                self.db.rafts.update_one(
                    {'day': date, 'slot': slot, 'raft_id': rid},
                    {'$set': {'occupancy': group_size, 'capacity': 6, 'is_special': False}},
                    upsert=True
                )
                self.created_raft_keys.append((date, slot, rid))

        insert_payment(
            self.db,
            booking_id=str(b_id),
            order_id=f'ADMIN_ORD_{b_id}',
            payment_id=f'ADMIN_PAY_{b_id}',
            signature='sig',
            amount=700.0,
            status='paid',
            raw_response={'mode': 'Cash'}
        )
        return b_id

    def test_scenario_1_both_histories_contain_records(self):
        """1. Both histories contain records: Records within the selected date range are deleted from both histories."""
        test_dates = ['2026-10-02', '2026-10-03', '2026-10-04']
        self._clean_dates(test_dates)
        self._login_admin()

        # Customer bookings inside range
        c1 = self._create_customer_booking('2026-10-02', status='Confirmed')
        c2 = self._create_customer_booking('2026-10-03', status='Pending')

        # Admin bookings inside range
        a1 = self._create_admin_booking('2026-10-02', status='Confirmed')
        a2 = self._create_admin_booking('2026-10-04', status='Pending')

        res = self.client.post('/admin/delete_records_by_date_range', json={
            'from': '2026-10-02',
            'to': '2026-10-04',
            'status': 'all'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertEqual(data.get('customer_deleted_count'), 2)
        self.assertEqual(data.get('admin_deleted_count'), 2)
        self.assertEqual(data.get('deleted_count'), 4)
        self.assertIn('2 customer booking(s)', data.get('message'))
        self.assertIn('2 admin booking(s)', data.get('message'))

        # Verify they are deleted from db.bookings
        self.assertIsNone(self.db.bookings.find_one({'_id': c1}))
        self.assertIsNone(self.db.bookings.find_one({'_id': c2}))
        self.assertIsNone(self.db.bookings.find_one({'_id': a1}))
        self.assertIsNone(self.db.bookings.find_one({'_id': a2}))

        # Verify associated payments are deleted
        self.assertIsNone(self.db.payments.find_one({'booking_id': str(c1)}))
        self.assertIsNone(self.db.payments.find_one({'booking_id': str(a1)}))

    def test_scenario_2_only_customer_history_contains_records(self):
        """2. Only customer history contains records: Customer records are deleted, admin cleanup executes without error."""
        test_dates = ['2026-10-05', '2026-10-06']
        self._clean_dates(test_dates)
        self._login_admin()

        c1 = self._create_customer_booking('2026-10-05', status='Confirmed')
        c2 = self._create_customer_booking('2026-10-06', status='Confirmed')

        res = self.client.post('/admin/delete_records_by_date_range', json={
            'from': '2026-10-05',
            'to': '2026-10-06',
            'status': 'all'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertEqual(data.get('customer_deleted_count'), 2)
        self.assertEqual(data.get('admin_deleted_count'), 0)
        self.assertEqual(data.get('deleted_count'), 2)
        self.assertIn('2 customer booking(s)', data.get('message'))
        self.assertNotIn('admin booking(s)', data.get('message'))

        self.assertIsNone(self.db.bookings.find_one({'_id': c1}))
        self.assertIsNone(self.db.bookings.find_one({'_id': c2}))

    def test_scenario_3_only_admin_history_contains_records(self):
        """3. Only admin history contains records: Admin records within selected range are deleted even when no customer records match."""
        test_dates = ['2026-10-07', '2026-10-08']
        self._clean_dates(test_dates)
        self._login_admin()

        a1 = self._create_admin_booking('2026-10-07', status='Confirmed')
        a2 = self._create_admin_booking('2026-10-08', status='Pending')

        res = self.client.post('/admin/delete_records_by_date_range', json={
            'from': '2026-10-07',
            'to': '2026-10-08',
            'status': 'all'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertEqual(data.get('customer_deleted_count'), 0)
        self.assertEqual(data.get('admin_deleted_count'), 2)
        self.assertEqual(data.get('deleted_count'), 2)
        self.assertIn('2 admin booking(s)', data.get('message'))
        self.assertNotIn('customer booking(s)', data.get('message'))

        self.assertIsNone(self.db.bookings.find_one({'_id': a1}))
        self.assertIsNone(self.db.bookings.find_one({'_id': a2}))

    def test_scenario_4_status_filter_applied(self):
        """4. Status filter applied: Customer deletion respects selected status, while admin-history cleanup covers entire date range."""
        test_dates = ['2026-10-09', '2026-10-10']
        self._clean_dates(test_dates)
        self._login_admin()

        # Customer bookings: 1 Confirmed (should be deleted), 1 Pending (should be preserved)
        c_conf = self._create_customer_booking('2026-10-09', status='Confirmed')
        c_pend = self._create_customer_booking('2026-10-09', status='Pending')

        # Admin bookings: 1 Confirmed, 1 Pending (BOTH should be deleted regardless of customer status filter)
        a_conf = self._create_admin_booking('2026-10-09', status='Confirmed')
        a_pend = self._create_admin_booking('2026-10-10', status='Pending')

        res = self.client.post('/admin/delete_records_by_date_range', json={
            'from': '2026-10-09',
            'to': '2026-10-10',
            'status': 'Confirmed'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertEqual(data.get('customer_deleted_count'), 1)
        self.assertEqual(data.get('admin_deleted_count'), 2)
        self.assertEqual(data.get('deleted_count'), 3)

        # Customer confirmed was deleted
        self.assertIsNone(self.db.bookings.find_one({'_id': c_conf}))
        # Customer pending was preserved!
        self.assertIsNotNone(self.db.bookings.find_one({'_id': c_pend}))

        # Admin confirmed and pending were BOTH deleted!
        self.assertIsNone(self.db.bookings.find_one({'_id': a_conf}))
        self.assertIsNone(self.db.bookings.find_one({'_id': a_pend}))

    def test_scenario_5_records_outside_range_preserved(self):
        """5. Records outside range: All records outside selected date range remain unchanged."""
        test_dates = ['2026-10-11', '2026-10-12', '2026-10-13', '2026-10-14']
        self._clean_dates(test_dates)
        self._login_admin()

        # Outside before (2026-10-11)
        c_before = self._create_customer_booking('2026-10-11', status='Confirmed')
        a_before = self._create_admin_booking('2026-10-11', status='Confirmed')

        # Inside range (2026-10-12 and 2026-10-13)
        c_inside = self._create_customer_booking('2026-10-12', status='Confirmed')
        a_inside = self._create_admin_booking('2026-10-13', status='Confirmed')

        # Outside after (2026-10-14)
        c_after = self._create_customer_booking('2026-10-14', status='Confirmed')
        a_after = self._create_admin_booking('2026-10-14', status='Confirmed')

        res = self.client.post('/admin/delete_records_by_date_range', json={
            'from': '2026-10-12',
            'to': '2026-10-13',
            'status': 'all'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('deleted_count'), 2)

        # Inside deleted
        self.assertIsNone(self.db.bookings.find_one({'_id': c_inside}))
        self.assertIsNone(self.db.bookings.find_one({'_id': a_inside}))

        # Outside preserved
        self.assertIsNotNone(self.db.bookings.find_one({'_id': c_before}))
        self.assertIsNotNone(self.db.bookings.find_one({'_id': a_before}))
        self.assertIsNotNone(self.db.bookings.find_one({'_id': c_after}))
        self.assertIsNotNone(self.db.bookings.find_one({'_id': a_after}))

    def test_scenario_6_date_boundaries_handled_correctly(self):
        """6. Date boundaries: Records on both start date and end date are handled correctly and inclusively."""
        test_dates = ['2026-10-16', '2026-10-17', '2026-10-18']
        self._clean_dates(test_dates)
        self._login_admin()

        # Exactly on start date
        c_start = self._create_customer_booking('2026-10-16', status='Confirmed')
        a_start = self._create_admin_booking('2026-10-16', status='Confirmed')

        # Exactly on end date
        c_end = self._create_customer_booking('2026-10-18', status='Confirmed')
        a_end = self._create_admin_booking('2026-10-18', status='Confirmed')

        res = self.client.post('/admin/delete_records_by_date_range', json={
            'from': '2026-10-16',
            'to': '2026-10-18',
            'status': 'all'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('customer_deleted_count'), 2)
        self.assertEqual(data.get('admin_deleted_count'), 2)
        self.assertEqual(data.get('deleted_count'), 4)

        self.assertIsNone(self.db.bookings.find_one({'_id': c_start}))
        self.assertIsNone(self.db.bookings.find_one({'_id': a_start}))
        self.assertIsNone(self.db.bookings.find_one({'_id': c_end}))
        self.assertIsNone(self.db.bookings.find_one({'_id': a_end}))

    def test_scenario_7_empty_date_range_results(self):
        """7. Empty date range results: Operation completes safely when neither history contains matching records."""
        test_dates = ['2026-10-19', '2026-10-20']
        self._clean_dates(test_dates)
        self._login_admin()

        res = self.client.post('/admin/delete_records_by_date_range', json={
            'from': '2026-10-19',
            'to': '2026-10-20',
            'status': 'all'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('deleted_count'), 0)
        self.assertEqual(data.get('customer_deleted_count'), 0)
        self.assertEqual(data.get('admin_deleted_count'), 0)
        self.assertIn('No matching customer or admin bookings found', data.get('message'))

    def test_scenario_8_database_error_handling(self):
        """8. Database error: System handles failures without incorrectly reporting success."""
        test_dates = ['2026-10-21', '2026-10-22']
        self._clean_dates(test_dates)
        self._login_admin()

        self._create_customer_booking('2026-10-21', status='Confirmed')
        self._create_admin_booking('2026-10-21', status='Confirmed')

        with patch.object(Collection, 'delete_many', side_effect=Exception('Simulated MongoDB failure')):
            res = self.client.post('/admin/delete_records_by_date_range', json={
                'from': '2026-10-21',
                'to': '2026-10-22',
                'status': 'all'
            })
            self.assertEqual(res.status_code, 500)
            data = res.get_json()
            self.assertIn('error', data)
            self.assertIn('Database error during deletion', data.get('error'))

    def test_scenario_9_raft_occupancy_freed_for_both_histories(self):
        """Verify raft occupancy is freed for both confirmed customer and admin bookings."""
        test_dates = ['2026-10-23', '2026-10-24']
        self._clean_dates(test_dates)
        self._login_admin()

        # Customer booking with raft allocation
        c_raft = self._create_customer_booking('2026-10-23', status='Confirmed', slot='7:00am', group_size=2, raft_allocations=[1])
        # Admin booking with raft allocation
        a_raft = self._create_admin_booking('2026-10-23', status='Confirmed', slot='7:00am', group_size=2, raft_allocations=[2])

        # Verify initial raft occupancy
        raft1 = self.db.rafts.find_one({'day': '2026-10-23', 'slot': '7:00am', 'raft_id': 1})
        raft2 = self.db.rafts.find_one({'day': '2026-10-23', 'slot': '7:00am', 'raft_id': 2})
        self.assertEqual(raft1.get('occupancy'), 2)
        self.assertEqual(raft2.get('occupancy'), 2)

        res = self.client.post('/admin/delete_records_by_date_range', json={
            'from': '2026-10-23',
            'to': '2026-10-23',
            'status': 'all'
        })
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get('freed_count'), 2)

        # Both bookings deleted
        self.assertIsNone(self.db.bookings.find_one({'_id': c_raft}))
        self.assertIsNone(self.db.bookings.find_one({'_id': a_raft}))

if __name__ == '__main__':
    unittest.main()
