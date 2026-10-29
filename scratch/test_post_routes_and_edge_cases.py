import utils.email_service as email_svc
email_svc.send_cancellation_email = lambda *a, **k: None
email_svc.send_postponement_email = lambda *a, **k: None
email_svc.send_coupon_email = lambda *a, **k: None
email_svc.send_group_size_edit_email = lambda *a, **k: None
email_svc.send_booking_email = lambda *a, **k: None

def run_post_tests():
    from app import create_app
    app = create_app()
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    mock_db = MockDatabase()
    class MongoNS:
        db = mock_db
        client = None
    app.mongo = MongoNS()

    # Set user_loader
    @app.login_manager.user_loader
    def load_user(user_id):
        return MockUser('supreme_admin')

    client = app.test_client()

    # Create dummy bookings in mock DB
    b_confirmed_id = mock_db.bookings.insert_one({
        'user_id': 'user@gmail.com',
        'user_name': 'Confirmed User',
        'email': 'user@gmail.com',
        'phone': '9876543210',
        'date': '2026-10-08',
        'slot': '7:00am',
        'group_size': 4,
        'status': 'Confirmed',
        'payment_status': 'Paid',
        'amount': 4600.0,
        'total_amount': 4600.0,
        'advance_amount': 1150.0,
        'balance_amount': 3450.0,
        'raft_allocations': [1],
        'raft_allocation_details': [{'raft_id': 1, 'count': 4}],
        'created_at': datetime.now(timezone.utc)
    }).inserted_id

    b_pending_id = mock_db.bookings.insert_one({
        'user_id': 'pending@gmail.com',
        'user_name': 'Pending User',
        'email': 'pending@gmail.com',
        'phone': '9876543211',
        'date': '2026-10-09',
        'slot': '10:00am',
        'group_size': 3,
        'status': 'Pending',
        'payment_status': 'Pending',
        'amount': 3450.0,
        'created_at': datetime.now(timezone.utc)
    }).inserted_id

    b_visited_id = mock_db.bookings.insert_one({
        'user_id': 'visited@gmail.com',
        'user_name': 'Visited User',
        'email': 'visited@gmail.com',
        'phone': '9876543212',
        'date': '2026-10-08',
        'slot': '1:00pm',
        'group_size': 5,
        'status': 'Visited',
        'payment_status': 'Pending',
        'amount': 5750.0,
        'created_at': datetime.now(timezone.utc)
    }).inserted_id

    # Authenticate session
    with client.session_transaction() as sess:
        sess['_user_id'] = '123456789012345678901234'
        sess['_fresh'] = True

    print("=== Testing POST /book ===")
    res1 = client.post('/book', data={
        'name': 'Test Book',
        'email': 'test@gmail.com',
        'phone': '9876543299',
        'location': 'Kochi',
        'booking_date': '2026-10-10',
        'slot': '7:00am',
        'group_size': '4'
    })
    print(f"POST /book -> Status: {res1.status_code}")
    if res1.status_code == 500:
        print("[FAIL 500] POST /book")

    print("\n=== Testing Payment Routes ===")
    res_order = client.post('/payment/create_order', json={'booking_id': str(b_visited_id)})
    print(f"POST /payment/create_order -> Status: {res_order.status_code}")

    res_verify = client.post('/payment/verify_payment', json={
        'booking_id': str(b_visited_id),
        'order_id': 'order_fake',
        'payment_id': 'pay_fake',
        'signature': 'invalid_sig'
    })
    print(f"POST /payment/verify_payment -> Status: {res_verify.status_code}")

    print("\n=== Testing Admin Actions ===")
    res_cancel = client.post(f'/admin/cancel_booking/{b_confirmed_id}')
    print(f"POST /admin/cancel_booking (confirmed) -> Status: {res_cancel.status_code}")

    res_cancel_pending = client.post(f'/admin/cancel_booking/{b_pending_id}')
    print(f"POST /admin/cancel_booking (pending) -> Status: {res_cancel_pending.status_code}")

    res_resize = client.post(f'/admin/reduce_group_size/{b_confirmed_id}', json={'new_size': 2})
    print(f"POST /admin/reduce_group_size -> Status: {res_resize.status_code}")

    res_rebook = client.post(f'/admin/rebook_pending/{b_pending_id}', json={'new_date': '2026-10-12', 'new_slot': '7:00am'})
    print(f"POST /admin/rebook_pending -> Status: {res_rebook.status_code}")

    res_postpone = client.post(f'/admin/postpone_booking/{b_confirmed_id}', json={'new_date': '2026-10-15', 'new_slot': '10:00am'})
    print(f"POST /admin/postpone_booking -> Status: {res_postpone.status_code}")

    res_coupon = client.post('/admin/generate_coupon', json={
        'phone': '9876543210',
        'email': 'customer@gmail.com',
        'group_size': 4,
        'discount_type': 'per_head',
        'discount_value': 100
    })
    print(f"POST /admin/generate_coupon -> Status: {res_coupon.status_code}")

    res_delete_range = client.post('/admin/delete_records_by_date_range', json={
        'from': '2026-10-01',
        'to': '2026-10-31',
        'status': 'all'
    })
    print(f"POST /admin/delete_records_by_date_range -> Status: {res_delete_range.status_code}")

if __name__ == '__main__':
    run_post_tests()
