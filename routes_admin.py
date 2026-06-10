from flask import Blueprint, jsonify
from auth_middleware import firebase_required
from models import db, User, Product, Order

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
    pending_orders = Order.query.filter(Order.status.in_(['Pending Payment', 'Accepted', 'Packed', 'Out For Delivery'])).count()
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

