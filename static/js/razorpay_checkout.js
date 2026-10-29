function openRazorpayCheckout(orderData) {
    var paymentAttemptStarted = false;

    function onMessage(event) {
        if (!event || !event.origin) return;
        if (event.origin.includes('razorpay.com')) {
            try {
                var data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
                if (data) {
                    var ev = (data.event || data.action || '').toLowerCase();
                    if (ev.includes('submit') || ev.includes('failed') || ev.includes('proceed') || ev.includes('pay') || ev.includes('interaction') || data.payment_id || data.error) {
                        console.log('Payment attempt detected via iframe message:', data);
                        paymentAttemptStarted = true;
                    }
                }
            } catch (e) {
                // Non-JSON iframe message
            }
        }
    }
    window.addEventListener('message', onMessage);

    var options = {
        "key": orderData.razorpay_key_id,
        "amount": Math.round(orderData.amount * 100),
        "currency": orderData.currency || "INR",
        "name": "Booking Payment",
        "order_id": orderData.order_id,
        "handler": function (response) {
            paymentAttemptStarted = true;
            window.removeEventListener('message', onMessage);

            fetch('/payment/verify_payment', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    booking_id: orderData.booking_id,
                    order_id: response.razorpay_order_id,
                    payment_id: response.razorpay_payment_id,
                    signature: response.razorpay_signature
                })
            })
                .then(function (res) {
                    if (!res.ok && res.status !== 400 && res.status !== 409) {
                        throw new Error('HTTP ' + res.status);
                    }
                    return res.json();
                })
                .then(function (data) {
                    if (data.success) {
                        window.location.replace('/payment/success?booking_id=' + encodeURIComponent(orderData.booking_id));
                    } else {
                        // Server explicitly confirmed verification failure
                        window.location.replace('/payment/failure?booking_id=' + encodeURIComponent(orderData.booking_id));
                    }
                })
                .catch(function (err) {
                    console.error('Communication error during payment verification:', err);
                    // Redirect to processing page for background/status verification on network error
                    window.location.replace('/payment/processing?booking_id=' + encodeURIComponent(orderData.booking_id));
                });
        },
        "modal": {
            "ondismiss": function () {
                window.removeEventListener('message', onMessage);
                console.log('Razorpay checkout dismissed. Directing to payment processing for status verification.');
                // Always route to processing flow so server-side status verification determines final result
                window.location.replace('/payment/processing?booking_id=' + encodeURIComponent(orderData.booking_id));
            }
        }
    };

    var rzp = new Razorpay(options);

    rzp.on('payment.failed', function (response) {
        console.log('Razorpay payment.failed event triggered:', response);
        paymentAttemptStarted = true;
    });

    rzp.open();
}



