from datetime import datetime

def insert_payment(db, booking_id, order_id, payment_id, signature, amount, status, raw_response, webhook_verified=False):
    payment = {
        "booking_id": booking_id,
        "order_id": order_id,
        "payment_id": payment_id,
        "signature": signature,
        "amount": amount,
        "status": status,
        "created_at": datetime.utcnow(),
        "raw_response": raw_response,
        "webhook_verified": webhook_verified
    }
    db.payments.insert_one(payment)
