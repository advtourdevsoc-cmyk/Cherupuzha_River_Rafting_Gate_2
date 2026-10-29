import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
from app import create_app

class TestSlotDateConnection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.db = cls.app.mongo.db

        cls.supreme = cls.db.users.find_one({'role': 'supreme_admin'})
        if not cls.supreme:
            cls.supreme = {'_id': ObjectId(), 'name': 'Supreme Tester', 'email': 'supreme_conn@raft.com', 'role': 'supreme_admin'}
            cls.db.users.insert_one(cls.supreme)

    def setUp(self):
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess['_user_id'] = str(self.supreme['_id'])
            sess['_fresh'] = True

        # Clean test slots
        self.db.immediate_slots.delete_many({'date': {'$in': ['2026-10-21', '2026-10-26']}})

    def tearDown(self):
        self.db.immediate_slots.delete_many({'date': {'$in': ['2026-10-21', '2026-10-26']}})

    def test_slot_filtration_connected_to_date(self):
        # Create immediate slot for 2026-10-21 only
        res_create = self.client.post('/admin/create_immediate_slot', json={'date': '2026-10-21', 'slot': '5:00pm'})
        self.assertEqual(res_create.status_code, 200)

        # 1. API endpoint for 2026-10-26 should NOT have 5:00pm
        res1 = self.client.get('/admin/api/slots_for_date_range?from=2026-10-26&to=2026-10-26')
        self.assertEqual(res1.status_code, 200)
        data1 = res1.get_json()
        self.assertNotIn('5:00pm', data1['slots'])
        self.assertIn('7:00am', data1['slots'])
        print('API for 2026-10-26 slots:', data1['slots'])

        # 2. API endpoint for 2026-10-21 SHOULD have 5:00pm
        res2 = self.client.get('/admin/api/slots_for_date_range?from=2026-10-21&to=2026-10-21')
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertIn('5:00pm', data2['slots'])
        print('API for 2026-10-21 slots:', data2['slots'])

        # 3. Dashboard HTML for 2026-10-26 should NOT have 5:00pm option in slotFilter
        res_dash_26 = self.client.get('/admin/dashboard?from=2026-10-26&to=2026-10-26&status=Confirmed')
        self.assertEqual(res_dash_26.status_code, 200)
        html_26 = res_dash_26.get_data(as_text=True)
        self.assertNotIn('value="5:00pm"', html_26)
        print('Dashboard on 2026-10-26 successfully excluded 5:00pm!')

        # 4. Dashboard HTML for 2026-10-21 SHOULD have 5:00pm option in slotFilter
        res_dash_21 = self.client.get('/admin/dashboard?from=2026-10-21&to=2026-10-21&status=Confirmed')
        self.assertEqual(res_dash_21.status_code, 200)
        html_21 = res_dash_21.get_data(as_text=True)
        self.assertIn('value="5:00pm"', html_21)
        print('Dashboard on 2026-10-21 successfully included 5:00pm!')

if __name__ == '__main__':
    unittest.main()
