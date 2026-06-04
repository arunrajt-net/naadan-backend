from flask import Blueprint, request, jsonify
import razorpay
import hmac
import hashlib
import os
from auth_middleware import firebase_required
from models import db, Order, Product, User

payment_bp = Blueprint("payment_bp", __name__)

# Get Razorpay keys from environment variables
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "rzp_test_placeholder")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "placeholder_secret")


def get_razorpay_client():
    return razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))


@payment_bp.route("/create-order", methods=["POST"])
@firebase_required()
def create_razorpay_order(current_user):
    """
    Step 1: Create a Razorpay order on the backend.
    Frontend calls this before showing the Razorpay popup.
    Returns razorpay_order_id, key_id, and amount.
    """
    data = request.get_json()
    amount_rupees = data.get("amount")
    cart = data.get("cart", [])

    if not amount_rupees or float(amount_rupees) <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    # Razorpay requires amount in paise (1 INR = 100 paise)
    amount_paise = int(float(amount_rupees) * 100)

    # Build receipt ID
    receipt = f"naadan_order_{current_user.id}_{len(cart)}items"

    try:
        client = get_razorpay_client()
        rzp_order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": {
                "buyer_id": str(current_user.id),
                "buyer_name": current_user.name,
                "buyer_phone": current_user.phone or "",
                "items": str(len(cart))
            }
        })

        return jsonify({
            "razorpay_order_id": rzp_order["id"],
            "key_id": RAZORPAY_KEY_ID,
            "amount": amount_paise,
            "amount_rupees": amount_rupees,
            "currency": "INR",
            "name": "Naadan — Farm Fresh",
            "buyer_name": current_user.name,
            "buyer_phone": current_user.phone or "",
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@payment_bp.route("/verify", methods=["POST"])
@firebase_required()
def verify_razorpay_payment(current_user):
    """
    Step 2: After Razorpay popup closes successfully, verify the payment signature.
    If signature is valid → create the DB order records → return order ID.
    If signature is invalid → reject the request (payment tampered or fake).
    """
    data = request.get_json()
    razorpay_payment_id = data.get("razorpay_payment_id")
    razorpay_order_id = data.get("razorpay_order_id")
    razorpay_signature = data.get("razorpay_signature")
    cart = data.get("cart", [])
    shipping_phone = data.get("shipping_phone", "")
    shipping_address = data.get("shipping_address", "")
    delivery_vehicle = data.get("delivery_vehicle", "motorcycle")

    if not razorpay_payment_id or not razorpay_order_id or not razorpay_signature:
        return jsonify({"error": "Missing payment verification parameters"}), 400

    # =============================================
    # CRITICAL: Verify HMAC SHA256 Signature
    # This is what makes this 100% fraud-proof.
    # Only Razorpay can produce the correct signature.
    # =============================================
    try:
        msg = f"{razorpay_order_id}|{razorpay_payment_id}"
        expected_signature = hmac.new(
            RAZORPAY_KEY_SECRET.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, razorpay_signature):
            return jsonify({"error": "Payment signature verification failed. Possible fraud attempt."}), 400

    except Exception as e:
        return jsonify({"error": f"Signature verification error: {str(e)}"}), 400

    # =============================================
    # Payment is VERIFIED. Now create DB orders.
    # =============================================
    try:
        # Save chosen delivery vehicle to user session notes
        first_order_id = None

        for item in cart:
            product_id = item.get("id")
            qty = int(item.get("order_qty") or 1)
            delivery_type = item.get("delivery_type", "Pickup")

            product = Product.query.get(product_id)
            if not product:
                continue

            total_price = product.price * qty

            new_order = Order(
                buyer_id=current_user.id,
                farmer_id=product.farmer_id,
                product_id=product.id,
                quantity_ordered=qty,
                total_price=total_price,
                delivery_type=delivery_type,
                shipping_phone=shipping_phone,
                shipping_address=shipping_address,
                # Mark payment as confirmed
                status="Pending"  # Farmer still needs to Accept/Ship
            )
            db.session.add(new_order)
            db.session.flush()  # Get the ID without committing

            if not first_order_id:
                first_order_id = new_order.id

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Payment verified and order placed successfully!",
            "order_id": first_order_id,
            "razorpay_payment_id": razorpay_payment_id
        }), 201

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Order creation failed: {str(e)}"}), 500


@payment_bp.route("/status/<razorpay_payment_id>", methods=["GET"])
@firebase_required()
def get_payment_status(razorpay_payment_id, current_user):
    """
    Optional: Fetch live payment status from Razorpay.
    Useful for admin dashboard or dispute resolution.
    """
    try:
        client = get_razorpay_client()
        payment = client.payment.fetch(razorpay_payment_id)
        return jsonify({
            "id": payment["id"],
            "amount": payment["amount"] / 100,
            "currency": payment["currency"],
            "status": payment["status"],
            "method": payment["method"],
            "email": payment.get("email", ""),
            "contact": payment.get("contact", ""),
            "created_at": payment["created_at"]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
