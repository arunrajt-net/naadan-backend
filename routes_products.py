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

    # Enforce Farmer Setup Guard (UPI ID, Location, Landmark)
    import re
    upi_id = (user.upi_id or "").strip()
    upi_regex = r'^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z]{2,64}$'
    
    errors = []
    # Only require UPI ID if farmer accepted payment methods include UPI (UPI_ONLY or BOTH or not configured yet)
    if user.payment_methods != 'COD_ONLY':
        if not upi_id or not re.match(upi_regex, upi_id):
            errors.append("UPI ID Missing or Invalid")
    if user.lat is None or user.lng is None or (user.lat == 10.0 and user.lng == 76.0):
        errors.append("GPS Location Not Configured")
    if not (user.pickup_landmark or "").strip():
        errors.append("Pickup Landmark Missing")
        
    if errors:
        msg = "Farmer setup incomplete: " + ", ".join(errors) + ". Complete missing fields before listing harvest."
        return jsonify({"msg": msg, "errors": errors}), 400

    data = request.get_json() or {}

    # Validate Price and Quantity
    price_val = data.get('price')
    qty_val = data.get('quantity')
    
    # Price check
    try:
        if price_val is None:
            return jsonify({"msg": "Price must be greater than zero."}), 400
        price_float = float(price_val)
        if price_float <= 0:
            return jsonify({"msg": "Price must be greater than zero."}), 400
    except (ValueError, TypeError):
        return jsonify({"msg": "Price must be greater than zero."}), 400

    # Quantity check
    try:
        if qty_val is None:
            return jsonify({"msg": "Quantity must be greater than zero."}), 400
        if isinstance(qty_val, (int, float)):
            qty_float = float(qty_val)
        else:
            import re
            qty_str = str(qty_val).strip()
            match = re.match(r'^([\d\.\-]+)\s*(.*)$', qty_str)
            if not match:
                return jsonify({"msg": "Quantity must be greater than zero."}), 400
            qty_float = float(match.group(1))
        
        if qty_float <= 0:
            return jsonify({"msg": "Quantity must be greater than zero."}), 400
    except (ValueError, TypeError):
        return jsonify({"msg": "Quantity must be greater than zero."}), 400
    
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
    products = Product.query.filter_by(farmer_id=user_id, is_deleted=False).all()
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

def obfuscate_coords(lat, lng, farmer_id):
    if lat is None or lng is None:
        return lat, lng
    import math
    angle = (farmer_id * 17) % 360
    shift_dist = 0.0065 + ((farmer_id * 31) % 100) * 0.000014
    rad = math.radians(angle)
    lat_offset = shift_dist * math.cos(rad)
    lng_offset = shift_dist * math.sin(rad)
    return round(lat + lat_offset, 6), round(lng + lng_offset, 6)

def get_area_info(lat, lng):
    if lat is None or lng is None:
        return "Unknown", "Unknown"
    # Major towns in Kerala
    towns = [
        {"name": "Kochi", "district": "Ernakulam", "lat": 9.9312, "lng": 76.2673},
        {"name": "Kottayam", "district": "Kottayam", "lat": 9.5916, "lng": 76.5222},
        {"name": "Alappuzha", "district": "Alappuzha", "lat": 9.4981, "lng": 76.3388},
        {"name": "Thrissur", "district": "Thrissur", "lat": 10.5276, "lng": 76.2144},
        {"name": "Trivandrum", "district": "Thiruvananthapuram", "lat": 8.5241, "lng": 76.9366},
        {"name": "Kozhikode", "district": "Kozhikode", "lat": 11.2588, "lng": 75.7804},
        {"name": "Palakkad", "district": "Palakkad", "lat": 10.7867, "lng": 76.6547},
        {"name": "Kannur", "district": "Kannur", "lat": 11.8745, "lng": 75.3704},
        {"name": "Kollam", "district": "Kollam", "lat": 8.8932, "lng": 76.6141},
        {"name": "Muvattupuzha", "district": "Ernakulam", "lat": 9.9894, "lng": 76.5790},
        {"name": "Thodupuzha", "district": "Idukki", "lat": 9.8959, "lng": 76.7184},
        {"name": "Kanjirappally", "district": "Kottayam", "lat": 9.5564, "lng": 76.7868},
        {"name": "Cherthala", "district": "Alappuzha", "lat": 9.6845, "lng": 76.3268},
        {"name": "Angamaly", "district": "Ernakulam", "lat": 10.1986, "lng": 76.3864},
    ]
    min_dist = float('inf')
    nearest_town = "Kerala"
    nearest_district = "Kerala"
    for t in towns:
        d = calculate_distance(lat, lng, t["lat"], t["lng"])
        if d < min_dist:
            min_dist = d
            nearest_town = t["name"]
            nearest_district = t["district"]
    return nearest_town, nearest_district

@products_bp.route('/nearby', methods=['GET'])
def get_nearby_products():
    from routes_orders import expire_abandoned_reservations
    expire_abandoned_reservations()
    lat = request.args.get('lat', type=float)
    lng = request.args.get('lng', type=float)
    radius = request.args.get('radius', default=10, type=float) # km
    
    if lat is None or lng is None:
        return jsonify({"msg": "lat and lng required"}), 400
        
    all_products = Product.query.filter_by(is_available=True, is_deleted=False).all()
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

                # Obfuscate coordinates only for public search if privacy is approximate
                p_lat, p_lng = p.lat, p.lng
                if farmer.location_privacy == "approximate":
                    p_lat, p_lng = obfuscate_coords(p.lat, p.lng, farmer.id)

                # Get town and district based on exact coordinates
                town, district = get_area_info(p.lat, p.lng)

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
                    "farmer_phone": "",
                    "farmer_upi_id": "",
                    "lat": p_lat,
                    "lng": p_lng,
                    "farmer_town": town,
                    "farmer_district": district,
                    "location_privacy": farmer.location_privacy,
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
                    "farmer_payment_methods": farmer.payment_methods
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
    if new_qty is None:
        return jsonify({"msg": "Quantity must be greater than zero."}), 400

    try:
        if isinstance(new_qty, (int, float)):
            qty_float = float(new_qty)
        else:
            import re
            qty_str = str(new_qty).strip()
            match = re.match(r'^([\d\.\-]+)\s*(.*)$', qty_str)
            if not match:
                return jsonify({"msg": "Quantity must be greater than zero."}), 400
            qty_float = float(match.group(1))
        
        if qty_float <= 0:
            return jsonify({"msg": "Quantity must be greater than zero."}), 400
    except (ValueError, TypeError):
        return jsonify({"msg": "Quantity must be greater than zero."}), 400

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

    from models import Order
    has_orders = Order.query.filter_by(product_id=product.id).first() is not None
    if has_orders:
        product.is_deleted = True
        product.is_available = False
        log_audit_event(current_user.id, "Product Soft Deleted", f"Soft deleted product {product.id} ({product.name}) because it has associated orders.")
    else:
        db.session.delete(product)
        log_audit_event(current_user.id, "Product Hard Deleted", f"Hard deleted product {product.id} ({product.name})")
    
    db.session.commit()
    
    return jsonify({"msg": "Product deleted successfully"}), 200
