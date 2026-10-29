import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

app = create_app()
with app.app_context():
    db = app.mongo.db
    b = db.bookings.find_one({'is_admin_booking': True})
    ca = b['created_at']
    start_utc = (ca - timedelta(minutes=5)).replace(tzinfo=timezone.utc)
    end_utc = (ca + timedelta(minutes=5)).replace(tzinfo=timezone.utc)
    start_naive = start_utc.replace(tzinfo=None)
    end_naive = end_utc.replace(tzinfo=None)

    query = {
        'is_admin_booking': True,
        '$or': [
            {'created_at': {'$gte': start_naive, '$lt': end_naive}},
            {'created_at': {'$gte': start_utc, '$lt': end_utc}},
            {'created_at': {'$gte': start_utc.isoformat(), '$lt': end_utc.isoformat()}},
        ]
    }
    cnt = db.bookings.count_documents(query)
    print("Or query count:", cnt)
