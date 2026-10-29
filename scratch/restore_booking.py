import os
from dotenv import load_dotenv
from pymongo import MongoClient
from bson.objectid import ObjectId

load_dotenv()
client = MongoClient(os.environ.get('MONGO_URI'))
db = client.get_default_database()

oid = ObjectId('6ad5a8527e1efd3ed0de3968')

db.bookings.update_one(
    {'_id': oid},
    {
        '$set': {
            'advance_amount': 1750.0,
            'booking_details.advance_amount': 1750.0,
            'balance_amount': 5292.0,
            'booking_details.balance_amount': 5292.0,
            'coupon_discount_applied': 2100.0,
            'booking_details.coupon_discount_applied': 2100.0,
            'coupon_code': 'RAFT-QN3S',
            'booking_details.coupon_code': 'RAFT-QN3S',
            'total_amount': 7042.0,
            'amount': 7042.0,
            'booking_details.total_amount': 7042.0,
            'price_amount': 7000.0,
            'booking_details.price_amount': 7000.0,
            'platform_fee': 42.0,
            'booking_details.platform_fee': 42.0,
            'original_total_amount': 9154.6,
            'booking_details.original_total_amount': 9154.6,
            'original_advance_amount': 2275.0,
            'booking_details.original_advance_amount': 2275.0,
            'amount_per_person': 1300.0,
            'booking_details.amount_per_person': 1300.0
        }
    }
)

b = db.bookings.find_one({'_id': oid})
print('=== RESTORED BOOKING ===')
for k in ['_id', 'user_name', 'date', 'slot', 'group_size', 'status', 'payment_status', 'advance_amount', 'balance_amount', 'total_amount', 'coupon_code', 'coupon_discount_applied', 'price_amount', 'platform_fee', 'original_total_amount', 'original_advance_amount']:
    print(f"{k}: {b.get(k)}")
