from flask import Blueprint, request, jsonify
from auth_middleware import firebase_required
from models import db, Order, Product, User, Rating, AuditEvent, Notification
from datetime import datetime, timedelta
from sqlalchemy import text

orders_bp = Blueprint('orders_bp', __name__)

# ================================================================
# ORDER STATUS FLOW:
# Pending Payment
#   -> (buyer marks paid)       -> Waiting Farmer Confirmation
#   -> (farmer confirms)        -> Accepted
#   -> (farmer packs)           -> Packed
#   -> (farmer ships)           -> Out For Delivery
#   -> (farmer delivers)        -> Waiting Customer Confirmation
#   -> (buyer confirms receipt) -> Completed
# Or:
#   Waiting Farmer Confirmation -> Rejected  (farmer rejects)
#   Out For Delivery/Waiting Customer Confirmation -> Disputed (buyer reports issue)
# ================================================================

def log_audit_event(user_id, action, details):
    try:
        event = AuditEvent(user_id=user_id, action=action, details=details)
        db.session.add(event)
        db.session.commit()
    except Exception as e:
        print(f"Failed to log audit event: {e}")

def create_notification(user_id, notif_type, message, order_id=None):
    try:
        notif = Notification(user_id=user_id, type=notif_type, message=message, order_id=order_id)
        db.session.add(notif)
        db.session.commit()
    except Exception as e:
        print(f"Failed to create notification: {e}")

def notify_status_change(order, status):
    buyer_id = order.buyer_id
    farmer_id = order.farmer_id
    
    product = Product.query.get(order.product_id)
    pname = product.name if product else "Product"
    
    if status == 'Pending Payment':
        create_notification(buyer_id, 'info', f"Your order #{order.id} has been placed successfully. Please complete payment.", order.id)
        create_notification(farmer_id, 'info', f"New order #{order.id} received for {pname}.", order.id)
    elif status == 'Waiting Farmer Confirmation':
        create_notification(buyer_id, 'info', f"Payment sent! Waiting for farmer to confirm order #{order.id}.", order.id)
        create_notification(farmer_id, 'warning', f"Payment submitted for order #{order.id}. Please confirm verification.", order.id)
    elif status == 'Accepted':
        create_notification(buyer_id, 'success', f"Farmer accepted your order #{order.id}! It is being prepared.", order.id)
        create_notification(farmer_id, 'success', f"You have accepted order #{order.id}.", order.id)
    elif status == 'Packed':
        create_notification(buyer_id, 'info', f"Your order #{order.id} has been packed.", order.id)
        create_notification(farmer_id, 'info', f"Order #{order.id} marked as Packed.", order.id)
    elif status == 'Out For Delivery':
        create_notification(buyer_id, 'order', f"Your order #{order.id} is out for delivery! Track it on the map.", order.id)
        create_notification(farmer_id, 'order', f"Order #{order.id} is out for delivery.", order.id)
    elif status == 'Waiting Customer Confirmation':
        create_notification(buyer_id, 'warning', f"Your order #{order.id} has been delivered! Please confirm receipt.", order.id)
        create_notification(farmer_id, 'info', f"Order #{order.id} delivered. Awaiting customer confirmation.", order.id)
    elif status == 'Completed':
        if order.completed_by == 'system':
            msg = f"Order #{order.id} auto-completed after delivery confirmation timeout."
            create_notification(buyer_id, 'success', msg, order.id)
            create_notification(farmer_id, 'success', msg, order.id)
        else:
            create_notification(buyer_id, 'success', f"You confirmed receipt of order #{order.id}.", order.id)
            create_notification(farmer_id, 'success', f"Customer confirmed delivery of order #{order.id}! Order completed.", order.id)
    elif status == 'Rejected':
        create_notification(buyer_id, 'error', f"Your order #{order.id} was rejected by the farmer.", order.id)
        create_notification(farmer_id, 'error', f"You have rejected order #{order.id}.", order.id)
    elif status == 'Cancelled':
        create_notification(buyer_id, 'error', f"You cancelled order #{order.id}.", order.id)
        create_notification(farmer_id, 'error', f"Customer cancelled order #{order.id}.", order.id)
    elif status == 'Disputed':
        create_notification(buyer_id, 'error', f"You reported an issue for order #{order.id}. Order is now Disputed.", order.id)
        create_notification(farmer_id, 'error', f"Customer reported an issue for order #{order.id}. Order is now Disputed.", order.id)

def expire_abandoned_reservations():
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=30)
        abandoned_orders = Order.query.filter(
            Order.status == 'Pending Payment',
            Order.created_at <= cutoff
        ).all()
        for order in abandoned_orders:
            product = Product.query.get(order.product_id)
            if product:
                product.reserved_quantity = max(0.0, (product.reserved_quantity or 0.0) - order.quantity_ordered)
            order.status = 'Expired'
            db.session.commit()
            log_audit_event(
                order.buyer_id,
                "Order Expired",
                f"Order {order.id} for product '{product.name if product else order.product_id}' expired automatically due to payment timeout."
            )
    except Exception as e:
        print(f"Error in expire_abandoned_reservations: {e}")

def auto_complete_delivered_orders():
    try:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        delivered_orders = Order.query.filter(
            Order.status == 'Waiting Customer Confirmation',
            Order.delivered_at <= cutoff
        ).all()
        for order in delivered_orders:
            order.status = 'Completed'
            order.completed_at = datetime.utcnow()
            order.completed_by = 'system'
            order.completion_reason = 'Auto-completed after delivery confirmation timeout.'
            
            # Finalize stock deduction upon completion
            product = Product.query.get(order.product_id)
            if product:
                val, unit = product.parse_quantity_str()
                new_val = max(0.0, val - order.quantity_ordered)
                if unit:
                    product.quantity = f"{new_val} {unit}"
                else:
                    product.quantity = str(new_val)
                # Permanently release reserved_quantity
                product.reserved_quantity = max(0.0, (product.reserved_quantity or 0.0) - order.quantity_ordered)
            
            # Update farmer stats
            farmer = User.query.get(order.farmer_id)
            if farmer:
                farmer.completed_orders_count = (farmer.completed_orders_count or 0) + 1
                farmer.compute_trust_score()
                
            db.session.commit()
            
            # Log transition audit event
            log_audit_event(None, "Order Status Updated", f"Order {order.id} status transitioned from 'Waiting Customer Confirmation' to 'Completed' (Auto-completed after delivery confirmation timeout.)")
            
            # Create notifications
            notify_status_change(order, 'Completed')
    except Exception as e:
        print(f"Error in auto_complete_delivered_orders: {e}")

@orders_bp.route('/', methods=['POST'])
@firebase_required()
def create_order(current_user):
    active_role = request.headers.get("X-Active-Role", "buyer")
    if active_role == 'farmer' and not current_user.is_farmer:
        return jsonify({"msg": "Forbidden. Farmer capability required."}), 403
    if active_role == 'buyer' and not current_user.is_buyer:
        return jsonify({"msg": "Forbidden. Buyer capability required."}), 403

    data = request.get_json() or {}
    
    idempotency_key = data.get('idempotency_key')
    if idempotency_key:
        existing = Order.query.filter_by(idempotency_key=idempotency_key).first()
        if existing:
            return jsonify({"msg": "Order placed", "id": existing.id, "status": existing.status}), 200

    expire_abandoned_reservations()
    auto_complete_delivered_orders()

    db.session.execute(text('BEGIN IMMEDIATE'))
    try:
        product_id = data.get('product_id')
        qty_raw = data.get('quantity_ordered') or data.get('quantity') or 1
        try:
            qty = max(1, min(int(qty_raw), 9999))
        except (ValueError, TypeError):
            qty = 1
        payment_method = data.get('payment_method', 'UPI')

        product = Product.query.get(product_id)
        if not product:
            db.session.rollback()
            return jsonify({"msg": "Product not found"}), 404

        if qty > product.available_stock:
            db.session.rollback()
            return jsonify({"msg": f"Only {product.available_stock} {product.unit} currently available."}), 400

        farmer = User.query.get(product.farmer_id)
        if not farmer:
            db.session.rollback()
            return jsonify({"msg": "Farmer not found"}), 404

        req_delivery_type = data.get('delivery_type') or data.get('delivery_method') or 'Pickup'
        if req_delivery_type.lower() == 'delivery' and not farmer.delivery_available:
            db.session.rollback()
            return jsonify({"msg": "Home Delivery is not available for this farmer's products. Please select 'Pickup from Farm'."}), 400

        total_price = product.price * qty

        initial_status = 'Waiting Farmer Confirmation' if payment_method == 'COD' else 'Pending Payment'
        payment_status = 'COD' if payment_method == 'COD' else 'Unpaid'

        new_order = Order(
            buyer_id=current_user.id,
            farmer_id=product.farmer_id,
            product_id=product.id,
            quantity_ordered=qty,
            total_price=total_price,
            status=initial_status,
            payment_status=payment_status,
            payment_method=payment_method,
            delivery_type=data.get('delivery_type', 'Pickup'),
            delivery_vehicle=data.get('delivery_vehicle', 'motorcycle'),
            shipping_phone=data.get('shipping_phone'),
            shipping_address=data.get('shipping_address'),
            idempotency_key=idempotency_key,
            
            # Snapshot coordinates
            buyer_lat=current_user.lat,
            buyer_lng=current_user.lng,
            farmer_lat=farmer.lat,
            farmer_lng=farmer.lng,
            product_lat=product.lat if product.lat is not None else farmer.lat,
            product_lng=product.lng if product.lng is not None else farmer.lng,
            
            # Snapshot product info
            product_name_snapshot=product.name,
            product_price_snapshot=product.price,
            product_category_snapshot=product.category,
            product_quantity_snapshot=product.quantity,
            product_unit_snapshot=product.unit,
            
            # Snapshot farmer & farm info
            farmer_name_snapshot=farmer.name,
            farm_name_snapshot=farmer.farm_name if farmer.farm_name else f"{farmer.name}'s Farm",
            pickup_instructions_snapshot=farmer.pickup_instructions,
            
            # Snapshot delivery settings
            delivery_type_snapshot=product.delivery_type,
            delivery_available_snapshot=farmer.delivery_available,
            delivery_price_per_km_snapshot=farmer.delivery_price_per_km,
            
            # Snapshot trust and verification status
            farmer_trust_score_snapshot=farmer.trust_score,
            farm_verification_status_snapshot=farmer.farm_verification_status,
            community_verification_status_snapshot=farmer.community_doc_status
        )
        db.session.add(new_order)
        product.reserved_quantity = (product.reserved_quantity or 0.0) + qty
        db.session.commit()
        
        log_audit_event(
            current_user.id, 
            "Order Created", 
            f"Placed order {new_order.id} for product '{product.name}' (Qty: {qty}, Total: {total_price})"
        )
        
        notify_status_change(new_order, initial_status)
        
        return jsonify({"msg": "Order placed", "id": new_order.id, "status": new_order.status}), 201
    except Exception as e:
        db.session.rollback()
        print(f"Error creating order: {e}")
        return jsonify({"msg": f"Failed to place order: {str(e)}"}), 500

@orders_bp.route('/<int:order_id>/pay', methods=['POST'])
@firebase_required()
def mark_paid(order_id, current_user):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"msg": "Order not found"}), 404
    
    if order.buyer_id != current_user.id:
        log_audit_event(current_user.id, "Permission Denied", f"Tried to pay for order {order_id} belonging to buyer {order.buyer_id}")
        return jsonify({"msg": "Unauthorized. You are not the buyer of this order."}), 403
        
    if order.status != 'Pending Payment':
        return jsonify({"msg": f"Order is already {order.status}"}), 400

    data = request.get_json() or {}
    order.status = 'Waiting Farmer Confirmation'
    order.payment_status = 'Paid'
    order.upi_ref = data.get('upi_ref', '')
    db.session.commit()
    
    log_audit_event(current_user.id, "Order Payment Submitted", f"Submitted payment confirmation for order {order.id} with UPI ref: {order.upi_ref}")
    
    notify_status_change(order, 'Waiting Farmer Confirmation')
    
    return jsonify({"msg": "Payment marked. Waiting for farmer confirmation.", "status": order.status}), 200

@orders_bp.route('/<int:order_id>/farmer-confirm', methods=['POST'])
@firebase_required()
def farmer_confirm(order_id, current_user):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"msg": "Order not found"}), 404
        
    if order.farmer_id != current_user.id:
        log_audit_event(current_user.id, "Permission Denied", f"Tried to confirm order {order_id} belonging to farmer {order.farmer_id}")
        return jsonify({"msg": "Only the farmer can confirm this order"}), 403
        
    if order.status != 'Waiting Farmer Confirmation':
        return jsonify({"msg": f"Order cannot be confirmed from status: {order.status}"}), 400

    order.status = 'Accepted'
    order.payment_status = 'Verified'
    db.session.commit()
    
    log_audit_event(current_user.id, "Order Confirmed", f"Farmer confirmed payment/order {order.id}")
    
    notify_status_change(order, 'Accepted')
    
    return jsonify({"msg": "Payment confirmed! Order is now Accepted.", "status": order.status}), 200

@orders_bp.route('/<int:order_id>/status', methods=['PUT'])
@firebase_required()
def update_order_status(order_id, current_user):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"msg": "Order not found"}), 404
        
    if order.farmer_id != current_user.id and order.buyer_id != current_user.id:
        log_audit_event(current_user.id, "Permission Denied", f"Tried to update status of order {order_id} belonging to buyer {order.buyer_id} and farmer {order.farmer_id}")
        return jsonify({"msg": "Unauthorized"}), 403

    data = request.get_json() or {}
    new_status = data.get('status')
    old_status = order.status

    if current_user.id == order.farmer_id:
        # Farmer transitions
        if new_status not in ['Accepted', 'Packed', 'Out For Delivery', 'Waiting Customer Confirmation', 'Rejected']:
            return jsonify({"msg": "Farmer is not allowed to set this status"}), 400
        if new_status == 'Rejected' and old_status != 'Waiting Farmer Confirmation':
            return jsonify({"msg": "Farmers can only reject orders waiting for confirmation"}), 400
        if new_status == 'Accepted' and old_status != 'Waiting Farmer Confirmation':
            return jsonify({"msg": "Cannot transition to Accepted from this status"}), 400
        if new_status == 'Packed' and old_status != 'Accepted':
            return jsonify({"msg": "Cannot transition to Packed from this status"}), 400
        if new_status == 'Out For Delivery' and old_status != 'Packed':
            return jsonify({"msg": "Cannot transition to Out For Delivery from this status"}), 400
        if new_status == 'Waiting Customer Confirmation' and old_status != 'Out For Delivery':
            return jsonify({"msg": "Cannot transition to Waiting Customer Confirmation from this status"}), 400

    elif current_user.id == order.buyer_id:
        # Buyer transitions
        if new_status not in ['Cancelled', 'Completed', 'Disputed']:
            return jsonify({"msg": "Buyers can only set Cancelled, Completed, or Disputed"}), 400
        if new_status == 'Cancelled' and old_status not in ['Pending Payment', 'Waiting Farmer Confirmation']:
            return jsonify({"msg": "Cannot cancel order after confirmation"}), 400
        if new_status == 'Completed' and old_status not in ['Out For Delivery', 'Waiting Customer Confirmation']:
            return jsonify({"msg": "Cannot confirm delivery from this status"}), 400
        if new_status == 'Disputed' and old_status not in ['Out For Delivery', 'Waiting Customer Confirmation']:
            return jsonify({"msg": "Cannot dispute order from this status"}), 400

    product = Product.query.get(order.product_id)
    
    # Handle stock reservation release or final deduction
    if new_status in ['Rejected', 'Cancelled']:
        if product:
            product.reserved_quantity = max(0.0, (product.reserved_quantity or 0.0) - order.quantity_ordered)
            
    elif new_status == 'Completed':
        order.completed_at = datetime.utcnow()
        order.completed_by = 'customer'
        order.completion_reason = 'Confirmed by customer'
        
        if product:
            val, unit = product.parse_quantity_str()
            new_val = max(0.0, val - order.quantity_ordered)
            if unit:
                product.quantity = f"{new_val} {unit}"
            else:
                product.quantity = str(new_val)
            product.reserved_quantity = max(0.0, (product.reserved_quantity or 0.0) - order.quantity_ordered)
            
        # Update farmer completed count & trust score
        farmer = User.query.get(order.farmer_id)
        if farmer:
            farmer.completed_orders_count = (farmer.completed_orders_count or 0) + 1
            farmer.compute_trust_score()

    elif new_status == 'Waiting Customer Confirmation':
        order.delivered_at = datetime.utcnow()

    elif new_status == 'Disputed':
        order.completion_reason = 'Disputed by customer'

    order.status = new_status
    db.session.commit()
    
    log_audit_event(current_user.id, "Order Status Updated", f"Order {order.id} status transitioned from '{old_status}' to '{new_status}'")
    
    notify_status_change(order, new_status)
    
    return jsonify({"msg": "Status updated", "new_status": new_status}), 200

@orders_bp.route('/<int:order_id>', methods=['GET'])
@firebase_required()
def get_order_detail(order_id, current_user):
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"msg": "Order not found"}), 404
        
    if order.farmer_id != current_user.id and order.buyer_id != current_user.id:
        log_audit_event(current_user.id, "Permission Denied", f"Tried to view detail of order {order_id} belonging to buyer {order.buyer_id} and farmer {order.farmer_id}")
        return jsonify({"msg": "Unauthorized"}), 403

    product = Product.query.get(order.product_id)
    farmer = User.query.get(order.farmer_id)
    buyer = User.query.get(order.buyer_id)

    # Freeze coordinates using snapshots. Fallback dynamically ONLY if snapshot is NULL.
    farmer_lat = order.farmer_lat if order.farmer_lat is not None else (product.lat if product and product.lat else (farmer.lat if farmer and farmer.lat else None))
    farmer_lng = order.farmer_lng if order.farmer_lng is not None else (product.lng if product and product.lng else (farmer.lng if farmer and farmer.lng else None))
    buyer_lat = order.buyer_lat if order.buyer_lat is not None else (buyer.lat if buyer and buyer.lat else None)
    buyer_lng = order.buyer_lng if order.buyer_lng is not None else (buyer.lng if buyer and buyer.lng else None)

    # Snapshots formatting
    product_name = order.product_name_snapshot if order.product_name_snapshot is not None else (product.name if product else "Unknown Product")
    product_price = order.product_price_snapshot if order.product_price_snapshot is not None else (product.price if product else 0.0)
    farmer_name = order.farmer_name_snapshot if order.farmer_name_snapshot is not None else (farmer.name if farmer else "Unknown Farmer")
    farm_name = order.farm_name_snapshot if order.farm_name_snapshot is not None else (farmer.farm_name if farmer else "Unknown Farm")
    pickup_instructions = order.pickup_instructions_snapshot if order.pickup_instructions_snapshot is not None else (farmer.pickup_instructions if (farmer and order.delivery_type == "Pickup") else "")
    pickup_landmark = farmer.pickup_landmark if farmer else ""
    buyer_name = buyer.name if buyer else "Unknown Buyer"

    return jsonify({
        "id": order.id,
        "buyer_id": order.buyer_id,
        "farmer_id": order.farmer_id,
        "product_name": product_name,
        "product_price": product_price,
        "quantity_ordered": order.quantity_ordered,
        "total_price": order.total_price,
        "status": order.status,
        "payment_status": order.payment_status,
        "payment_method": order.payment_method,
        "upi_ref": order.upi_ref,
        "delivery_type": order.delivery_type,
        "delivery_vehicle": order.delivery_vehicle,
        "created_at": order.created_at.isoformat() if order.created_at else "",
        "farmer_name": farmer_name,
        "farm_name": farm_name,
        "farmer_phone": farmer.phone if farmer else "",
        "farmer_upi_id": (farmer.upi_id or (farmer.phone + "@upi" if farmer.phone else "")) if farmer else "",
        "farmer_lat": farmer_lat,
        "farmer_lng": farmer_lng,
        "buyer_name": buyer_name,
        "buyer_phone": order.shipping_phone or (buyer.phone if buyer else ""),
        "buyer_address": order.shipping_address,
        "buyer_lat": buyer_lat,
        "buyer_lng": buyer_lng,
        "pickup_instructions": pickup_instructions,
        "pickup_landmark": pickup_landmark,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "completed_at": order.completed_at.isoformat() if order.completed_at else None,
        "completed_by": order.completed_by,
        "completion_reason": order.completion_reason,
        
        # Extra snapshot payload for details
        "product_category": order.product_category_snapshot,
        "product_quantity": order.product_quantity_snapshot,
        "product_unit": order.product_unit_snapshot,
        "delivery_available_snapshot": order.delivery_available_snapshot,
        "delivery_price_per_km_snapshot": order.delivery_price_per_km_snapshot,
        "farmer_trust_score_snapshot": order.farmer_trust_score_snapshot,
        "farm_verification_status_snapshot": order.farm_verification_status_snapshot,
        "community_verification_status_snapshot": order.community_verification_status_snapshot
    }), 200

@orders_bp.route('/farmer', methods=['GET'])
@firebase_required()
def get_farmer_orders(current_user):
    if not current_user.is_farmer:
        return jsonify({"msg": "Forbidden. Farmer capability required."}), 403
        
    expire_abandoned_reservations()
    auto_complete_delivered_orders()
    
    orders = Order.query.filter_by(farmer_id=current_user.id).order_by(Order.created_at.desc()).all()
    res = []
    for o in orders:
        product = Product.query.get(o.product_id)
        buyer = User.query.get(o.buyer_id)
        res.append({
            "id": o.id,
            "product_name": product.name if product else "?",
            "buyer_name": buyer.name if buyer else "?",
            "buyer_phone": o.shipping_phone or (buyer.phone if buyer else ""),
            "buyer_address": o.shipping_address,
            "quantity_ordered": o.quantity_ordered,
            "total_price": o.total_price,
            "status": o.status,
            "payment_status": o.payment_status,
            "payment_method": o.payment_method,
            "upi_ref": o.upi_ref,
            "delivery_type": o.delivery_type,
            "delivery_vehicle": o.delivery_vehicle,
            "created_at": o.created_at.isoformat() if o.created_at else "",
            "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
            "completed_at": o.completed_at.isoformat() if o.completed_at else None,
            "completed_by": o.completed_by,
            "completion_reason": o.completion_reason
        })
    return jsonify(res), 200

@orders_bp.route('/buyer', methods=['GET'])
@firebase_required()
def get_buyer_orders(current_user):
    if not current_user.is_buyer:
        return jsonify({"msg": "Forbidden. Buyer capability required."}), 403

    expire_abandoned_reservations()
    auto_complete_delivered_orders()

    orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.created_at.desc()).all()
    res = []
    for o in orders:
        product = Product.query.get(o.product_id)
        farmer = User.query.get(o.farmer_id)
        res.append({
            "id": o.id,
            "product_name": product.name if product else "?",
            "farmer_name": farmer.name if farmer else "?",
            "farmer_phone": farmer.phone if farmer else "",
            "quantity_ordered": o.quantity_ordered,
            "total_price": o.total_price,
            "status": o.status,
            "payment_status": getattr(o, 'payment_status', None),
            "payment_method": getattr(o, 'payment_method', 'UPI'),
            "delivery_type": o.delivery_type,
            "created_at": o.created_at.isoformat() if o.created_at else "",
            "upi_ref": getattr(o, 'upi_ref', None),
            "delivered_at": o.delivered_at.isoformat() if o.delivered_at else None,
            "completed_at": o.completed_at.isoformat() if o.completed_at else None,
            "completed_by": o.completed_by,
            "completion_reason": o.completion_reason
        })
    return jsonify(res), 200

@orders_bp.route('/<int:order_id>/rate', methods=['POST'])
@firebase_required()
def rate_order(order_id, current_user):
    order = Order.query.get(order_id)
    if not order or order.buyer_id != current_user.id:
        return jsonify({"msg": "Not found or unauthorized"}), 404
    
    # Rating only allowed for Completed orders
    if order.status != 'Completed':
        return jsonify({"msg": "Ratings are only allowed after the order is completed."}), 400

    data = request.get_json() or {}
    rating_val = data.get('rating')
    feedback = data.get('feedback', '')
    
    try:
        rating_int = max(1, min(5, int(rating_val or 5)))
    except:
        return jsonify({"msg": "Rating must be 1-5"}), 400

    existing = Rating.query.filter_by(order_id=order.id, buyer_id=current_user.id).first()
    if existing:
        existing.rating = rating_int
        existing.feedback = feedback
        existing.created_at = datetime.utcnow()
    else:
        new_r = Rating(
            farmer_id=order.farmer_id,
            buyer_id=current_user.id,
            order_id=order.id,
            rating=rating_int,
            feedback=feedback
        )
        db.session.add(new_r)
    db.session.commit()

    # Recalculate average rating, total ratings, and trust score
    farmer = User.query.get(order.farmer_id)
    if farmer:
        all_ratings = Rating.query.filter_by(farmer_id=order.farmer_id).all()
        total = sum(r.rating for r in all_ratings)
        count = len(all_ratings)
        farmer.average_rating = round(total / count, 1)
        farmer.total_ratings = count
        farmer.compute_trust_score()
        db.session.commit()
    
    log_audit_event(current_user.id, "Order Rated", f"Rated order {order.id} with score {rating_int}")
    
    return jsonify({"msg": "Rating submitted"}), 200

# ================================================================
# NOTIFICATION API ENDPOINTS:
# ================================================================
@orders_bp.route('/notifications', methods=['GET'])
@firebase_required()
def get_notifications(current_user):
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(50).all()
    res = []
    for n in notifs:
        res.append({
            "id": n.id,
            "type": n.type,
            "message": n.message,
            "orderId": n.order_id,
            "read": n.read,
            "time": n.created_at.isoformat() if n.created_at else ""
        })
    return jsonify(res), 200

@orders_bp.route('/notifications/mark-read', methods=['POST'])
@firebase_required()
def mark_notifications_read(current_user):
    notifs = Notification.query.filter_by(user_id=current_user.id, read=False).all()
    for n in notifs:
        n.read = True
    db.session.commit()
    return jsonify({"msg": "Notifications marked read"}), 200

@orders_bp.route('/notifications', methods=['DELETE'])
@firebase_required()
def clear_notifications(current_user):
    Notification.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({"msg": "Notifications cleared"}), 200
