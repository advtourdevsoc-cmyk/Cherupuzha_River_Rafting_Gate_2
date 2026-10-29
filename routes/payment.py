"""
Razorpay Payment Integration Blueprint
"""
import os
import logging
from datetime import datetime

from bson import ObjectId
from flask import Blueprint, request, jsonify, current_app, render_template
from flask_login import login_required, current_user
from werkzeug.exceptions import BadRequest
from pymongo import ReturnDocument
import razorpay

from models.booking_model import create_booking, update_booking_status, get_booking
from models.payment_model import insert_payment
from utils.allocation_logic import load_settings, allocate_raft
from utils.amount_calculator import calculate_total_amount


payment_bp = Blueprint("payment", __name__, url_prefix="/payment")

logger = logging.getLogger("payment")
logger.setLevel(logging.INFO)

# In production, prefer configuring these via environment / Config
razorpay_client = razorpay.Client(
    auth=(
        os.environ.get("RAZORPAY_KEY_ID", "rzp_test_SLoMv8ODDZOJqO"),
        os.environ.get("RAZORPAY_KEY_SECRET", "RaGDqalgbz3me6HEIBi5yQxV"),
    )
)


def get_db():
    """
    Always use the same database handle as the rest of the app.
    This avoids subtle bugs where create_booking and payment flows
    talk to different default databases.
    """
    return current_app.mongo.db


@payment_bp.route("/create_order", methods=["POST"])
def create_order():
    """
    Create a Razorpay order for an existing booking.

    - Does NOT create a new booking (avoids duplicates).
    - Is idempotent per booking: if an order already exists, it is reused.
    """
    data = request.get_json() or {}

    booking_id = data.get("booking_id")
    if not booking_id:
        logger.warning("create_order called without booking_id")
        return jsonify({"error": "booking_id is required"}), 400

    db = get_db()
    try:
        booking = db.bookings.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        logger.exception("Invalid booking id supplied to create_order")
        return jsonify({"error": "Invalid booking id"}), 400

    if not booking:
        logger.warning("Booking not found for create_order: %s", booking_id)
        return jsonify({"error": "Booking not found"}), 404

    # Basic state guard: do not allow payment for cancelled / already-confirmed bookings
    status = (booking.get("status") or "").lower()
    if status in {"cancelled", "failed"}:
        logger.info("Payment requested for non-payable booking %s with status=%s", booking_id, status)
        return jsonify({"error": "Booking is not in a payable state"}), 400
    currency = booking.get("currency", "INR")

    # Use stored pricing first to prevent settings changes from modifying existing bookings
    total_amount = booking.get("amount") or booking.get("total_amount")
    advance_amount = booking.get("advance_amount")
    
    # Fallback to settings calculation only for legacy bookings missing these fields
    if advance_amount is None or total_amount is None:
        settings = load_settings(db)
        try:
            group_size = int(booking.get("group_size", 0) or 0)
        except (TypeError, ValueError):
            group_size = 0
            
        pricing = calculate_total_amount(settings, booking.get("date"), group_size)
        total_amount = total_amount if total_amount is not None else (pricing.get("total_amount") or booking.get("amount"))
        advance_amount = advance_amount if advance_amount is not None else pricing.get("advance_amount")

    # Fallback: if advance could not be computed, charge total to avoid zero orders
    amount_to_charge = advance_amount if advance_amount and advance_amount > 0 else total_amount

    if not amount_to_charge or amount_to_charge <= 0:
        logger.warning("Invalid payable amount for %s: %s", booking_id, amount_to_charge)
        return jsonify({"error": "Failed to calculate amount"}), 400

    existing_order_id = booking.get("razorpay_order_id")
    order_id = existing_order_id

    if existing_order_id:
        logger.info(
            "Reusing existing Razorpay order %s for booking %s", existing_order_id, booking_id
        )
    else:
        amount_paise = int(amount_to_charge * 100)
        order = razorpay_client.order.create(
            {
                "amount": amount_paise,
                "currency": currency,
                "payment_capture": 1,
            }
        )
        order_id = order["id"]
        db.bookings.update_one(
            {"_id": booking["_id"]},
            {
                "$set": {
                    "razorpay_order_id": order_id,
                    # ensure payment_status is set to Pending on order creation
                    "payment_status": "Pending",
                    "payment_order_created_at": datetime.utcnow(),
                }
            },
        )
        logger.info("Razorpay order %s created for booking %s", order_id, booking_id)

    return jsonify(
        {
            "order_id": order_id,
            # Expose the ADVANCE amount to the frontend / Razorpay checkout
            "amount": amount_to_charge,
            "currency": currency,
            "booking_id": str(booking["_id"]),
            # Must match the key used in razorpay_client
            "razorpay_key_id": os.environ.get("RAZORPAY_KEY_ID", "rzp_test_SLoMv8ODDZOJqO"),
        }
    )


def confirm_booking_payment(db, booking, payment_id, order_id=None, signature=None, raw_response=None):
    """
    Unified, idempotent helper to process successful payment confirmation.
    Can be invoked by verify_payment, webhook, or status polling when a payment is captured.

    Guarantees:
    - Safe execution when called multiple times.
    - Raft capacity allocation occurs AFTER payment.
    - If allocation fails (capacity race), booking stays 'Paid' but status 'Pending' + allocation_error=True.
    - Marks coupons as used atomically.
    - Records payment entry in db.payments.
    - Sends booking confirmation email safely (without breaking on email errors).
    """
    booking_id_str = str(booking["_id"])
    order_id = order_id or booking.get("razorpay_order_id")

    # Fetch fresh booking state from DB to avoid stale in-memory data
    fresh_booking = db.bookings.find_one({"_id": booking["_id"]})
    if fresh_booking:
        booking = fresh_booking

    # Idempotency check: if already confirmed and paid, do not repeat side effects
    if booking.get("status") == "Confirmed" and booking.get("payment_status") == "Paid":
        logger.info("confirm_booking_payment: Booking %s already confirmed & paid", booking_id_str)
        return {"success": True, "already_processed": True, "booking": booking}

    if booking.get("status") == "Cancelled":
        logger.warning("confirm_booking_payment: Booking %s is cancelled", booking_id_str)
        return {"success": False, "cancelled": True, "message": "Booking is cancelled. Please contact support."}

    # STEP 1: Allocate rafts AFTER successful payment
    raft_allocations = booking.get("raft_allocations") or []
    raft_details = booking.get("raft_allocation_details") or []

    booking_date = booking.get("date") or booking.get("booking_details", {}).get("date")
    slot = booking.get("slot") or booking.get("booking_details", {}).get("slot")

    if not raft_allocations:
        try:
            group_size = int(
                booking.get("group_size")
                or booking.get("booking_details", {}).get("group_size")
                or 0
            )
        except (TypeError, ValueError):
            group_size = 0

        if not booking_date or not slot or group_size <= 0:
            logger.error("confirm_booking_payment: Incomplete booking details for %s", booking_id_str)
            return {"success": False, "message": "Booking data incomplete for seat allocation."}

        alloc_res = allocate_raft(db, None, booking_date, slot, group_size)
        if alloc_res.get("status") != "Confirmed":
            # Check if concurrently confirmed
            fresh_check = db.bookings.find_one({"_id": booking["_id"]})
            if fresh_check and fresh_check.get("status") == "Confirmed":
                logger.info("confirm_booking_payment: Booking %s was concurrently confirmed", booking_id_str)
                return {"success": True, "already_processed": True, "booking": fresh_check}

            logger.error(
                "Allocation failed after payment for booking_id=%s date=%s slot=%s group_size=%s",
                booking_id_str, booking_date, slot, group_size
            )
            from utils.booking_ops import recompute_occupancy_for_slot
            recompute_occupancy_for_slot(db, booking_date, slot)

            update_booking_status(
                db,
                booking_id_str,
                status="Pending",
                payment_status="Paid",
                extra_updates={"allocation_error": True},
            )
            return {
                "success": True,
                "payment_status": "Paid",
                "booking_status": "Pending",
                "allocation_error": True,
                "message": "Payment received but slot is now full. Our team will contact you.",
                "booking_id": booking_id_str,
            }

        raft_allocations = alloc_res.get("rafts", []) or []
        raft_details = alloc_res.get("raft_details", []) or []

    # STEP 2: Update booking to Confirmed + Paid in single atomic operation
    updated = db.bookings.find_one_and_update(
        {"_id": booking["_id"]},
        {
            "$set": {
                "status": "Confirmed",
                "payment_status": "Paid",
                "razorpay_payment_id": payment_id,
                "payment_verified_at": datetime.utcnow(),
                "raft_allocations": raft_allocations,
                "raft_allocation_details": raft_details,
                "allocation_error": False,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        updated = db.bookings.find_one({"_id": booking["_id"]})

    # Mark coupon as used if applicable
    coupon_code = updated.get("coupon_code")
    if coupon_code:
        try:
            from models.coupon_model import mark_coupon_used
            mark_coupon_used(db, coupon_code, str(updated["_id"]))
        except Exception as e:
            logger.error("Failed to mark coupon %s as used for booking %s: %s", coupon_code, booking_id_str, e)

    # Record payment in db.payments (idempotent)
    try:
        existing_payment = db.payments.find_one({"payment_id": payment_id})
        if not existing_payment:
            insert_payment(
                db,
                booking_id=booking_id_str,
                order_id=order_id,
                payment_id=payment_id,
                signature=signature or "",
                amount=updated.get("amount"),
                status="paid",
                raw_response=raw_response or {},
            )
        else:
            db.payments.update_one(
                {"_id": existing_payment["_id"]},
                {"$set": {"status": "paid", "raw_response": raw_response or existing_payment.get("raw_response", {})}},
            )
    except Exception as e:
        logger.error("Failed to record payment in db.payments: %s", e)

    # Send success email asynchronously
    try:
        from utils.email_service import send_booking_email, send_email_async
        send_email_async(send_booking_email, updated, is_success=True)
    except Exception as e:
        logger.error("Failed to send payment success email: %s", e)

    return {"success": True, "payment_status": "Paid", "booking_status": "Confirmed", "booking": updated}


@payment_bp.route("/verify_payment", methods=["POST"])
def verify_payment():
    """
    Verify Razorpay payment signature and update booking/payment atomically.

    Rules:
    - Never mark a booking as Confirmed before signature verification.
    - Use razorpay_order_id (server-side value) to locate the booking.
    - Idempotent: if the same payment is verified twice, do not double-update.
    """
    data = request.get_json() or {}

    booking_id = data.get("booking_id")
    order_id = data.get("order_id")
    payment_id = data.get("payment_id")
    signature = data.get("signature")

    if not order_id or not payment_id or not signature:
        logger.warning("verify_payment missing required fields: %s", data)
        return jsonify({"error": "order_id, payment_id and signature are required"}), 400

    db = get_db()

    # First, verify the Razorpay payment signature
    try:
        razorpay_client.utility.verify_payment_signature(
            {
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            }
        )
    except razorpay.errors.SignatureVerificationError:
        logger.error(
            "Signature verification failed for order_id=%s payment_id=%s booking_id=%s",
            order_id,
            payment_id,
            booking_id,
        )
        # Explicit signature failure: mark payment as Failed
        from utils.email_service import send_booking_email
        failed_booking_data = None
        if booking_id:
            try:
                failed_booking_data = db.bookings.find_one({"_id": ObjectId(booking_id)})
                update_booking_status(db, booking_id, status="Pending", payment_status="Failed")
            except Exception:
                logger.exception("Failed to update booking status after signature failure")
        else:
            failed_booking_data = db.bookings.find_one({"razorpay_order_id": order_id})
            db.bookings.update_one(
                {"razorpay_order_id": order_id},
                {"$set": {"status": "Pending", "payment_status": "Failed"}},
            )
            
        if failed_booking_data:
            try:
                from utils.email_service import send_booking_email, send_email_async
                send_email_async(send_booking_email, failed_booking_data, is_success=False)
            except Exception as e:
                logger.error("Failed to send payment failure email: %s", e)
                
        return jsonify({"success": False, "message": "Payment verification failed"}), 400

    # Locate booking by server-controlled razorpay_order_id
    booking_filter = {"razorpay_order_id": order_id}
    if booking_id:
        try:
            booking_filter["_id"] = ObjectId(booking_id)
        except Exception:
            logger.warning("verify_payment received invalid booking_id %s; ignoring id filter", booking_id)

    booking = db.bookings.find_one(booking_filter)
    if not booking:
        logger.error(
            "No booking found for verified payment. order_id=%s booking_id=%s",
            order_id,
            booking_id,
        )
        return jsonify({"success": False, "message": "Booking not found for this payment"}), 404

    res = confirm_booking_payment(db, booking, payment_id, order_id=order_id, signature=signature, raw_response=data)
    
    if res.get("cancelled"):
        return jsonify({"success": False, "message": res.get("message")}), 409

    if res.get("allocation_error") or res.get("booking_status") == "Pending":
        return jsonify({
            "success": False,
            "payment_status": "Paid",
            "booking_status": "Pending",
            "allocation_error": True,
            "message": res.get("message"),
            "booking_id": str(booking["_id"])
        }), 200

    return jsonify({"success": True, "message": "Payment successful", "booking_id": str(booking["_id"])})


@payment_bp.route("/status/<booking_id>", methods=["GET"])
def payment_status(booking_id):
    """
    Check the current payment and booking status for a given booking ID.
    Distinguishes between:
      - 'paid': Payment confirmed/captured
      - 'failed': Payment explicitly failed
      - 'unpaid': Confirmed no payment attempt made (order unpaid & 0 attempts)
      - 'pending': Verification unknown / API delay / in-progress verification
    """
    db = get_db()
    try:
        booking = db.bookings.find_one({"_id": ObjectId(booking_id)})
    except Exception:
        return jsonify({"error": "Invalid booking ID"}), 400

    if not booking:
        return jsonify({"error": "Booking not found"}), 404

    payment_status_val = (booking.get("payment_status") or "Pending").strip()
    booking_status_val = (booking.get("status") or "Pending").strip()
    allocation_error_val = bool(booking.get("allocation_error", False))

    # 1. If already Paid or Confirmed in DB
    if payment_status_val.lower() == "paid" or booking_status_val.lower() == "confirmed":
        return jsonify({
            "payment_status": "paid",
            "booking_status": booking_status_val,
            "allocation_error": allocation_error_val,
            "booking_id": str(booking["_id"])
        })

    # 2. If explicitly Failed in DB
    if payment_status_val.lower() == "failed" or booking_status_val.lower() == "failed":
        return jsonify({
            "payment_status": "failed",
            "booking_status": booking_status_val,
            "allocation_error": False,
            "booking_id": str(booking["_id"])
        })

    # 3. If DB status is still Pending, check Razorpay order details & payments
    order_id = booking.get("razorpay_order_id")
    if order_id:
        try:
            # Query Razorpay API for order payments
            payments_res = razorpay_client.order.payments(order_id)
            items = payments_res.get("items", []) if isinstance(payments_res, dict) else []

            # Check if any payment attempt was captured or authorized
            captured_payment = next((p for p in items if p.get("status") in ["captured", "authorized"]), None)
            if captured_payment:
                payment_id = captured_payment.get("id")
                logger.info("Found captured payment %s in Razorpay for booking %s during status polling", payment_id, booking_id)
                confirm_res = confirm_booking_payment(db, booking, payment_id, order_id=order_id, raw_response=captured_payment)
                
                return jsonify({
                    "payment_status": "paid",
                    "booking_status": confirm_res.get("booking_status", "Confirmed"),
                    "allocation_error": bool(confirm_res.get("allocation_error", False)),
                    "booking_id": str(booking["_id"])
                })

            # Check if payment items exist and ALL of them explicitly failed
            if items and all(p.get("status") == "failed" for p in items):
                logger.info("All payment attempts failed in Razorpay for booking %s", booking_id)
                update_booking_status(db, booking_id, status="Pending", payment_status="Failed")
                return jsonify({
                    "payment_status": "failed",
                    "booking_status": "Pending",
                    "allocation_error": False,
                    "booking_id": str(booking["_id"])
                })

            # Query Razorpay order info to check amount_paid and order status
            order_info = razorpay_client.order.fetch(order_id)
            amount_paid = order_info.get("amount_paid", 0) if isinstance(order_info, dict) else 0
            order_status = order_info.get("status", "") if isinstance(order_info, dict) else ""

            # Confirmed UNPAID / NO PAYMENT ATTEMPT:
            # Razorpay API explicitly confirms 0 payment items exist, 0 amount paid, and status is 'created'
            if len(items) == 0 and amount_paid == 0 and order_status == "created":
                logger.info("Razorpay order %s for booking %s confirmed unpaid (0 payment attempts)", order_id, booking_id)
                return jsonify({
                    "payment_status": "unpaid",
                    "booking_status": booking_status_val,
                    "allocation_error": False,
                    "booking_id": str(booking["_id"])
                })

        except Exception as e:
            logger.error("Error fetching Razorpay order status for order %s / booking %s: %s", order_id, booking_id, e)
            # CRITICAL: API error or timeout MUST safely return 'pending' (NEVER 'unpaid' or 'failed')

    return jsonify({
        "payment_status": "pending",
        "booking_status": booking_status_val,
        "allocation_error": allocation_error_val,
        "booking_id": str(booking["_id"])
    })


@payment_bp.route("/processing", methods=["GET"])
def payment_processing():
    booking_id = request.args.get("booking_id", "")
    return render_template("payment_processing.html", booking_id=booking_id), 200


@payment_bp.route("/webhook", methods=["POST"])
def razorpay_webhook():
    """
    Handle Razorpay webhook events (idempotent).
    """
    payload = request.data
    signature = request.headers.get("X-Razorpay-Signature")
    webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
    db = get_db()

    try:
        razorpay_client.utility.verify_webhook_signature(payload, signature, webhook_secret)
    except razorpay.errors.SignatureVerificationError:
        logger.error("Webhook signature verification failed")
        return jsonify({"success": False}), 400

    body = request.json or {}
    event = body.get("event")
    payment_entity = body.get("payload", {}).get("payment", {}).get("entity", {}) or {}

    order_id = payment_entity.get("order_id")
    payment_id = payment_entity.get("id")
    status = payment_entity.get("status")

    # Ensure we only process each payment id once
    payment_record = db.payments.find_one({"payment_id": payment_id})
    if payment_record and payment_record.get("webhook_verified"):
        logger.info("Webhook already processed for payment %s", payment_id)
        return jsonify({"success": True})

    db.payments.update_one(
        {"payment_id": payment_id},
        {
            "$set": {
                "webhook_verified": True,
                "status": status,
                "webhook_payload": body,
            }
        },
        upsert=True,
    )

    if event == "payment.captured" and status == "captured":
        booking = db.bookings.find_one({"razorpay_order_id": order_id})
        if booking:
            confirm_booking_payment(db, booking, payment_id, order_id=order_id, raw_response=body)
    elif event == "payment.failed":
        booking = db.bookings.find_one({"razorpay_order_id": order_id})
        if booking and booking.get("status") not in ["Confirmed", "Cancelled"]:
            update_booking_status(db, str(booking["_id"]), status="Pending", payment_status="Failed")

    logger.info("Webhook processed: %s for payment %s", event, payment_id)
    return jsonify({"success": True})


@payment_bp.route("/success", methods=["GET"])
def payment_success():
    booking_id = request.args.get("booking_id")
    if booking_id:
        try:
            db = get_db()
            booking = db.bookings.find_one({"_id": ObjectId(booking_id)})
            if booking:
                status_str = str(booking.get("status", "")).strip().lower()
                if status_str != "confirmed":
                    return redirect(url_for("payment.payment_failure", booking_id=booking_id))
        except Exception as e:
            logger.error("Error in payment_success checking booking status: %s", e)
    return render_template("payment_success.html"), 200


@payment_bp.route("/failure", methods=["GET"])
def payment_failure():
    booking_id = request.args.get("booking_id")
    if booking_id:
        try:
            db = get_db()
            booking = db.bookings.find_one({"_id": ObjectId(booking_id)})
            status = booking.get("status")
            payment_status_val = booking.get("payment_status")
            if booking and status not in ["Confirmed", "Cancelled"] and payment_status_val != "Paid":
                # Explicit failure view requested
                update_booking_status(db, booking_id, status="Pending", payment_status="Failed")
                from utils.email_service import send_booking_email, send_email_async
                send_email_async(send_booking_email, booking, is_success=False)
        except Exception as e:
            logger.error("Failed to process payment failure view for %s: %s", booking_id, e)

    return render_template("payment_failure.html"), 200

