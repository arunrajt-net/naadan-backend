from flask import Blueprint, jsonify
from auth_middleware import firebase_required
from models import db, User, Product, Order, SMSAuditLog

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/gps-health', methods=['GET'])
@firebase_required()
def get_gps_health(current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Forbidden. Admin capability required."}), 403
        
    # Stats
    total_farmers = User.query.filter_by(is_farmer=True).count()
    farmers_missing_gps = User.query.filter(User.is_farmer == True, (User.lat == None) | (User.lng == None)).count()
    
    total_products = Product.query.count()
    products_missing_gps = Product.query.filter((Product.lat == None) | (Product.lng == None)).count()
    
    # Locations outside Kerala boundaries roughly:
    # Latitude: 8.15 to 12.85, Longitude: 74.85 to 77.5
    farmers_outside_kerala = User.query.filter(
        User.is_farmer == True,
        (User.lat != None) & (User.lng != None) & (
            (User.lat < 8.15) | (User.lat > 12.85) | (User.lng < 74.85) | (User.lng > 77.5)
        )
    ).count()
    
    # Duplicate coordinates count
    from sqlalchemy import func
    duplicates_query = db.session.query(User.lat, User.lng, func.count(User.id)).filter(
        User.is_farmer == True,
        User.lat != None,
        User.lng != None
    ).group_by(User.lat, User.lng).having(func.count(User.id) > 1).all()
    duplicate_gps_count = len(duplicates_query)
    
    # Orders without frozen coordinates
    orders_without_snapshots = Order.query.filter(Order.farmer_lat == None).count()
    
    return jsonify({
        "total_farmers": total_farmers,
        "farmers_missing_gps": farmers_missing_gps,
        "total_products": total_products,
        "products_missing_gps": products_missing_gps,
        "farmers_outside_kerala": farmers_outside_kerala,
        "duplicate_gps_count": duplicate_gps_count,
        "orders_without_snapshots": orders_without_snapshots
    }), 200


@admin_bp.route('/dashboard-stats', methods=['GET'])
@firebase_required()
def get_dashboard_stats(current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Forbidden. Admin capability required."}), 403

    total_farmers = User.query.filter_by(is_farmer=True).count()
    total_buyers = User.query.filter_by(is_buyer=True).count()

    total_products = Product.query.count()
    active_products = Product.query.filter_by(is_available=True).count()

    total_orders = Order.query.count()
    pending_orders = Order.query.filter(Order.status.in_(['Pending Payment', 'Accepted', 'Packed', 'Out For Delivery', 'COD_PENDING', 'COD_ACCEPTED'])).count()
    completed_orders = Order.query.filter(Order.status.in_(['Completed', 'COMPLETED'])).count()

    # Recent Activity:
    # 1. Last 5 registrations
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    recent_users_list = []
    for u in recent_users:
        recent_users_list.append({
            "id": u.id,
            "name": u.name,
            "role": u.role,
            "created_at": u.created_at.isoformat() if u.created_at else None
        })

    # 2. Last 5 product listings
    recent_products = Product.query.order_by(Product.created_at.desc()).limit(5).all()
    recent_products_list = []
    for p in recent_products:
        recent_products_list.append({
            "id": p.id,
            "name": p.name,
            "price": p.price,
            "quantity": p.quantity,
            "created_at": p.created_at.isoformat() if p.created_at else None
        })

    # 3. Last 5 orders
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    recent_orders_list = []
    for o in recent_orders:
        recent_orders_list.append({
            "id": o.id,
            "quantity_ordered": o.quantity_ordered,
            "total_price": o.total_price,
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None
        })

    return jsonify({
        "stats": {
            "total_farmers": total_farmers,
            "total_buyers": total_buyers,
            "total_products": total_products,
            "active_products": active_products,
            "total_orders": total_orders,
            "pending_orders": pending_orders,
            "completed_orders": completed_orders
        },
        "recent_activity": {
            "users": recent_users_list,
            "products": recent_products_list,
            "orders": recent_orders_list
        }
    }), 200


@admin_bp.route('/sms-stats', methods=['GET'])
@firebase_required()
def get_sms_stats(current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Forbidden. Admin capability required."}), 403

    from datetime import datetime, time as dtime
    
    now = datetime.utcnow()
    today_start = datetime.combine(now.date(), dtime.min)
    month_start = datetime(now.year, now.month, 1)

    sent_today = SMSAuditLog.query.filter(SMSAuditLog.created_at >= today_start, SMSAuditLog.delivery_status != 'FAILED').count()
    sent_this_month = SMSAuditLog.query.filter(SMSAuditLog.created_at >= month_start, SMSAuditLog.delivery_status != 'FAILED').count()
    
    otp_count = SMSAuditLog.query.filter(SMSAuditLog.event_type.in_(['OTP_RECOVERY', 'OTP_SIGNUP']), SMSAuditLog.delivery_status != 'FAILED').count()
    order_alert_count = SMSAuditLog.query.filter(SMSAuditLog.event_type == 'NEW_ORDER_ALERT', SMSAuditLog.delivery_status != 'FAILED').count()
    failed_count = SMSAuditLog.query.filter(SMSAuditLog.delivery_status == 'FAILED').count()
    
    # Calculate successful SMS total count
    successful_sms = SMSAuditLog.query.filter(SMSAuditLog.delivery_status != 'FAILED').count()
    estimated_cost = round(successful_sms * 0.20, 2)  # 20 paise per SMS

    return jsonify({
        "sent_today": sent_today,
        "sent_this_month": sent_this_month,
        "otp_count": otp_count,
        "order_alert_count": order_alert_count,
        "failed_count": failed_count,
        "estimated_cost": estimated_cost
    }), 200

@admin_bp.route('/payments', methods=['GET'])
@firebase_required()
def get_payments(current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Forbidden. Admin capability required."}), 403
        
    orders = Order.query.filter((Order.payment_method == 'UPI') | (Order.payment_screenshot_url != None)).order_by(Order.created_at.desc()).all()
    res = []
    for o in orders:
        buyer = User.query.get(o.buyer_id)
        farmer = User.query.get(o.farmer_id)
        res.append({
            "id": o.id,
            "buyer_name": buyer.name if buyer else "Unknown Buyer",
            "farmer_name": farmer.name if farmer else "Unknown Farmer",
            "total_price": o.total_price,
            "status": o.status,
            "payment_status": o.payment_status,
            "payment_screenshot_url": o.payment_screenshot_url,
            "utr_number": o.utr_number,
            "payment_verified_at": o.payment_verified_at.isoformat() if o.payment_verified_at else None,
            "payment_verified_by": o.payment_verified_by,
            "payment_rejection_reason": o.payment_rejection_reason,
            "created_at": o.created_at.isoformat() if o.created_at else None
        })
    return jsonify(res), 200


