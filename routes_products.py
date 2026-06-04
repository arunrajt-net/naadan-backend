from flask import Blueprint, request, jsonify
from auth_middleware import firebase_required
from models import db, Product, User, Order, Rating, AuditEvent
import math

products_bp = Blueprint('products_bp', __name__)

def log_audit_event(user_id, action, details):
    try:
        event = AuditEvent(user_id=user_id, action=action, details=details)
        db.session.add(event)
        db.session.commit()
    except Exception as e:
        print(f"Failed to log audit event: {e}")

def calculate_distance(lat1, lon1, lat2, lon2):
    # Haversine formula
    R = 6371 # earth radius in km
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    a = math.sin(dLat/2) * math.sin(dLat/2) +         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *         math.sin(dLon/2) * math.sin(dLon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

@products_bp.route('/', methods=['POST'])
@firebase_required()
def add_product(current_user):
    user = current_user
    print(f"DEBUG_LISTING: User details -> ID: {user.id}, Name: {user.name}, Role: {user.role}, UID: {user.firebase_uid}")
    
    # Enforce role security (Condition 3)
    if not user.is_farmer:
        log_audit_event(user.id, "Permission Denied", "Attempted to add product without is_farmer flag")
        return jsonify({"msg": "Only farmers can add products"}), 403

    data = request.get_json() or {}
    
    # 2. Client-Side Idempotency Key check (Condition 4)
    idempotency_key = data.get('idempotency_key')
    if idempotency_key:
        existing = Product.query.filter_by(idempotency_key=idempotency_key).first()
        if existing:
            print(f"[DEBUG_PRODUCTS] Duplicate product creation detected via idempotency key: {idempotency_key}")
            return jsonify({"msg": "Product added successfully", "id": existing.id}), 200

    lat = data.get('lat')
    lng = data.get('lng')
    if lat is None or lng is None:
        lat = user.lat if user.lat is not None else 10.0
        lng = user.lng if user.lng is not None else 76.0

    new_product = Product(
        farmer_id=user.id,
        name=data.get('name'),
        category=data.get('category'),
        price=data.get('price'),
        quantity=data.get('quantity'),
        image_url=data.get('image_url'),
        lat=lat,
        lng=lng,
        idempotency_key=idempotency_key
    )
    db.session.add(new_product)
    db.session.commit()
    
    log_audit_event(user.id, "Product Created", f"Added product: {new_product.name} (ID: {new_product.id}, Qty: {new_product.quantity})")
    
    return jsonify({"msg": "Product added successfully", "id": new_product.id}), 201

@products_bp.route('/farmer', methods=['GET'])
@firebase_required()
def get_farmer_products(current_user):
    # Enforce role security
    if not current_user.is_farmer:
        return jsonify({"msg": "Forbidden. Farmer capability required."}), 403
        
    user_id = current_user.id
    products = Product.query.filter_by(farmer_id=user_id).all()
    res = []
    for p in products:
        res.append({
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "price": p.price,
            "quantity": p.quantity,
            "image_url": p.image_url,
            "is_available": p.is_available,
            "total_stock": p.total_stock,
            "reserved_stock": p.reserved_stock,
            "available_stock": p.available_stock,
            "unit": p.unit,
            "sold_quantity": p.sold_quantity
        })
    return jsonify(res), 200

@products_bp.route('/nearby', methods=['GET'])
def get_nearby_products():
    from routes_orders import expire_abandoned_reservations
    expire_abandoned_reservations()
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    radius = request.args.get('radius', default=10, type=float) # km
    
    if lat is None or lng is None:
        return jsonify({"msg": "lat and lng required"}), 400
        
    all_products = Product.query.filter_by(is_available=True).all()
    results = []
    
    for p in all_products:
        if p.lat and p.lng:
            dist = calculate_distance(lat, lng, p.lat, p.lng)
            if dist <= radius:
                farmer = User.query.get(p.farmer_id)
                if not farmer:
                    continue
                
                # Fetch ratings
                ratings = Rating.query.filter_by(farmer_id=farmer.id).all()
                avg_rating = round(sum(r.rating for r in ratings) / len(ratings), 1) if ratings else None
                
                # Calculate completed orders
                completed_count = Order.query.filter(
                    Order.farmer_id == farmer.id,
                    Order.status.in_(['Completed', 'COMPLETED'])
                ).count()
                
                # Update trust score before returning
                farmer.compute_trust_score()
                db.session.commit()

                results.append({
                    "id": p.id,
                    "farmer_id": p.farmer_id,
                    "name": p.name,
                    "category": p.category,
                    "price": p.price,
                    "quantity": f"{p.available_stock} {p.unit}",
                    "image_url": p.image_url,
                    "distance_km": round(dist, 2),
                    "farmer_name": farmer.name,
                    "farmer_phone": farmer.phone,
                    "farmer_upi_id": farmer.upi_id or (farmer.phone + "@upi" if farmer.phone else ""),
                    "farmer_rating": avg_rating,
                    "farmer_rating_count": len(ratings),
                    "farmer_is_verified": bool(farmer.is_verified),
                    "farmer_completed_orders": completed_count,
                    "delivery_available": bool(farmer.delivery_available) if farmer.delivery_available is not None else False,
                    "delivery_price_per_km": float(farmer.delivery_price_per_km or 0.0),
                    "farmer_phone_verified": bool(farmer.phone_verified or farmer.phone),
                    "farmer_farm_verified": bool(farmer.farm_verified),
                    "farmer_community_verified": bool(farmer.community_verified),
                    "farmer_trust_score": float(farmer.trust_score or 0.0),
                    "farmer_farm_verification_status": farmer.farm_verification_status or "NONE",
                    "farmer_community_doc_status": farmer.community_doc_status or "NONE",
                    "available_stock": p.available_stock,
                    "unit": p.unit,
                })
                
    # Sort by distance
    results.sort(key=lambda x: x['distance_km'])
    return jsonify(results), 200

@products_bp.route('/<int:product_id>/restock', methods=['PUT'])
@firebase_required()
def restock_product(product_id, current_user):
    # Enforce role security
    if not current_user.is_farmer:
        return jsonify({"msg": "Forbidden. Farmer capability required."}), 403

    product = Product.query.get(product_id)
    if not product:
        return jsonify({"msg": "Product not found"}), 404
    
    # Ownership verification (Condition 8 / Test H)
    if product.farmer_id != current_user.id:
        log_audit_event(current_user.id, "Permission Denied", f"Tried to restock product {product_id} belonging to farmer {product.farmer_id}")
        return jsonify({"msg": "Unauthorized. You do not own this product listing."}), 403

    data = request.get_json() or {}
    new_qty = data.get('quantity')
    if not new_qty:
        return jsonify({"msg": "quantity parameter required"}), 400

    old_qty = product.quantity
    product.quantity = str(new_qty).strip()
    product.is_available = True
    db.session.commit()
    
    log_audit_event(current_user.id, "Stock Updated", f"Restocked product {product.id} ({product.name}) from '{old_qty}' to '{product.quantity}'")
    
    return jsonify({"msg": "Product restocked successfully", "quantity": product.quantity}), 200

@products_bp.route('/<int:product_id>', methods=['DELETE'])
@firebase_required()
def delete_product(product_id, current_user):
    # Enforce role security
    if not current_user.is_farmer:
        return jsonify({"msg": "Forbidden. Farmer capability required."}), 403

    product = Product.query.get(product_id)
    if not product:
        return jsonify({"msg": "Product not found"}), 404
        
    # Ownership verification (Condition 8 / Test H)
    if product.farmer_id != current_user.id:
        log_audit_event(current_user.id, "Permission Denied", f"Tried to delete product {product_id} belonging to farmer {product.farmer_id}")
        return jsonify({"msg": "Unauthorized. You do not own this product listing."}), 403

    db.session.delete(product)
    db.session.commit()
    
    log_audit_event(current_user.id, "Product Deleted", f"Deleted product {product.id} ({product.name})")
    
    return jsonify({"msg": "Product deleted successfully"}), 200
