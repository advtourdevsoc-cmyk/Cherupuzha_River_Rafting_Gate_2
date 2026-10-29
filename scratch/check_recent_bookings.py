import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()
client = MongoClient(os.environ.get('MONGO_URI'))
db = client.get_default_database()

for b in db.bookings.find({'status': {'$in': ['Confirmed', 'Pending']}}).sort('created_at', -1).limit(10):
    c_code = b.get('coupon_code') or b.get('booking_details', {}).get('coupon_code')
    disc = b.get('coupon_discount_applied')
    adv = b.get('advance_amount')
    bal = b.get('balance_amount')
    tot = b.get('total_amount')
    status = b.get('status')
    date = b.get('date')
    slot = b.get('slot')
    oid = str(b.get('_id'))
    print(f"ID: {oid} | Date: {date} {slot} | Status: {status} | Adv: {adv} | Bal: {bal} | Tot: {tot} | Coupon: {c_code} ({disc})")
