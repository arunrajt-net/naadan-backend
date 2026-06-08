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
