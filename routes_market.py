from flask import Blueprint, request, jsonify
from auth_middleware import firebase_required
from models import db, MarketPrice, User

market_bp = Blueprint('market_bp', __name__)

# --- Market Prices API ---

@market_bp.route('/all', methods=['GET'])
def get_all_market_prices():
    prices = MarketPrice.query.order_by(MarketPrice.product_name, MarketPrice.price.desc()).all()
    return jsonify([{
        "id": p.id,
        "productName": p.product_name,
        "marketName": p.market_name,
        "price": p.price,
        "location": p.location,
        "lat": p.lat,
        "lng": p.lng,
        "date": p.created_at.strftime('%Y-%m-%d')
    } for p in prices]), 200

@market_bp.route('/best/<product_name>', methods=['GET'])
def get_best_price(product_name):
    matches = MarketPrice.query.filter(MarketPrice.product_name.ilike(f'%{product_name}%')).order_by(MarketPrice.price.desc()).all()
    if not matches:
        return jsonify({"msg": "No market price found"}), 404
    
    best = matches[0]
    return jsonify({
        "id": best.id,
        "productName": best.product_name,
        "marketName": best.market_name,
        "price": best.price,
        "location": best.location,
        "lat": best.lat,
        "lng": best.lng,
        "date": best.created_at.strftime('%Y-%m-%d'),
        "all_markets": [{
            "id": m.id,
            "productName": m.product_name,
            "marketName": m.market_name,
            "price": m.price,
            "location": m.location,
            "lat": m.lat,
            "lng": m.lng,
            "date": m.created_at.strftime('%Y-%m-%d')
        } for m in matches]
    }), 200

@market_bp.route('/add', methods=['POST'])
@firebase_required()
def add_market_price(current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Unauthorized. Admin role required."}), 403
    
    data = request.get_json()
    product_name = data.get('productName')
    market_name = data.get('marketName')
    price = float(data.get('price') or 0)
    location = data.get('location')
    lat = data.get('lat')
    lng = data.get('lng')

    if not product_name or not market_name or not price or not location:
        return jsonify({"msg": "Missing required fields"}), 400

    new_price = MarketPrice(
        product_name=product_name,
        market_name=market_name,
        price=price,
        location=location,
        lat=lat,
        lng=lng
    )
    db.session.add(new_price)
    db.session.commit()
    return jsonify({"msg": "Market price added successfully", "id": new_price.id}), 201

@market_bp.route('/update/<int:price_id>', methods=['PUT'])
@firebase_required()
def update_market_price(price_id, current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Unauthorized. Admin role required."}), 403
    
    item = MarketPrice.query.get(price_id)
    if not item:
        return jsonify({"msg": "Market price item not found"}), 404

    data = request.get_json()
    item.product_name = data.get('productName', item.product_name)
    item.market_name = data.get('marketName', item.market_name)
    item.price = float(data.get('price') or item.price)
    item.location = data.get('location', item.location)
    if 'lat' in data:
        item.lat = data.get('lat')
    if 'lng' in data:
        item.lng = data.get('lng')

    db.session.commit()
    return jsonify({"msg": "Market price updated successfully"}), 200

@market_bp.route('/delete/<int:price_id>', methods=['DELETE'])
@firebase_required()
def delete_market_price(price_id, current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Unauthorized. Admin role required."}), 403
    
    item = MarketPrice.query.get(price_id)
    if not item:
        return jsonify({"msg": "Market price item not found"}), 404

    db.session.delete(item)
    db.session.commit()
    return jsonify({"msg": "Market price item deleted"}), 200


# --- Farmer Verification API ---

@market_bp.route('/verify/submit', methods=['POST'])
@firebase_required()
def submit_verification(current_user):
    if not current_user.is_farmer:
        return jsonify({"msg": "Only farmers can submit verification details"}), 403

    data = request.get_json()
    aadhaar = data.get('aadhaar_number')
    panchayat = data.get('panchayat_id')
    phone = data.get('phone')

    # Phone is mandatory for verification
    if not phone and not current_user.phone:
        return jsonify({"msg": "Phone verification is mandatory. Please provide a phone number."}), 400

    if phone:
        current_user.phone = phone

    current_user.aadhaar_number = aadhaar
    current_user.panchayat_id = panchayat
    current_user.verification_status = 'PENDING'
    db.session.commit()

    return jsonify({
        "msg": "Verification details submitted successfully. Under review.",
        "status": current_user.verification_status
    }), 200

@market_bp.route('/verify/pending', methods=['GET'])
@firebase_required()
def get_pending_verifications(current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Unauthorized. Admin role required."}), 403

    pending_farmers = User.query.filter_by(is_farmer=True, verification_status='PENDING').all()
    return jsonify([{
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "phone": u.phone,
        "aadhaar_number": u.aadhaar_number,
        "panchayat_id": u.panchayat_id,
        "created_at": u.created_at.strftime('%Y-%m-%d')
    } for u in pending_farmers]), 200

@market_bp.route('/verify/approve/<int:user_id>', methods=['POST'])
@firebase_required()
def approve_verification(user_id, current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Unauthorized. Admin role required."}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"msg": "User not found"}), 404

    user.is_verified = True
    user.verification_status = 'VERIFIED'
    db.session.commit()
    return jsonify({"msg": "Farmer verification approved", "userId": user.id}), 200

@market_bp.route('/verify/reject/<int:user_id>', methods=['POST'])
@firebase_required()
def reject_verification(user_id, current_user):
    if not current_user.is_admin:
        return jsonify({"msg": "Unauthorized. Admin role required."}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({"msg": "User not found"}), 404

    user.is_verified = False
    user.verification_status = 'NONE'
    db.session.commit()
    return jsonify({"msg": "Farmer verification rejected", "userId": user.id}), 200


# --- Upgraded Farmer Market Intelligence & Event Logging ---

@market_bp.route('/event', methods=['POST'])
def log_market_event():
    data = request.get_json() or {}
    event_type = data.get('event_type')
    payload_dict = data.get('payload', {})
    if not event_type:
        return jsonify({"msg": "event_type required"}), 400
    
    import json
    from models import AnalyticsEvent
    new_event = AnalyticsEvent(
        event_type=event_type,
        payload=json.dumps(payload_dict)
    )
    db.session.add(new_event)
    db.session.commit()
    return jsonify({"msg": "Event logged successfully"}), 201

@market_bp.route('/intelligence', methods=['GET'])
def get_market_intelligence():
    from routes_orders import expire_abandoned_reservations
    expire_abandoned_reservations()
    crop = request.args.get('crop', '').strip()
    if not crop:
        return jsonify({"msg": "crop parameter required"}), 400

    from models import Product, MarketPrice, AnalyticsEvent, Order, Rating
    import json, re

    # 1. Get active listings
    products = Product.query.filter(Product.name.ilike(f'%{crop}%'), Product.is_available == True).all()
    count = len(products)

    # Helper function to parse quantity
    def parse_quantity(qty_str):
        match = re.findall(r'[\d\.]+', str(qty_str))
        if not match:
            return 1.0
        val = float(match[0])
        qty_str_lower = str(qty_str).lower()
        if "g" in qty_str_lower and "kg" not in qty_str_lower:
            val = val / 1000.0
        return val

    # Helper to calculate median
    def calculate_median(lst):
        if not lst:
            return 0.0
        sorted_lst = sorted(lst)
        n = len(sorted_lst)
        if n % 2 == 1:
            return sorted_lst[n // 2]
        else:
            return (sorted_lst[n // 2 - 1] + sorted_lst[n // 2]) / 2.0

    total_qty = sum(p.available_stock for p in products)

    # 2. Base prices calculation
    if count > 0:
        prices = [p.price for p in products]
        min_price = min(prices)
        max_price = max(prices)
        median_price = calculate_median(prices)
        is_fallback = False
    else:
        # Fallback to seeded MarketPrice table
        seeded = MarketPrice.query.filter(MarketPrice.product_name.ilike(f'%{crop}%')).all()
        if seeded:
            prices = [s.price for s in seeded]
            min_price = min(prices)
            max_price = max(prices)
            median_price = calculate_median(prices)
            is_fallback = True
        else:
            # Absolute default fallback
            min_price = 35.0
            max_price = 55.0
            median_price = 45.0
            is_fallback = True

    # 3. Competition Indicator
    if count <= 2:
        competition = "Low"
    elif count <= 7:
        competition = "Medium"
    else:
        competition = "High"

    # 4. Analytics query (demand metrics)
    searches = AnalyticsEvent.query.filter(
        AnalyticsEvent.event_type == 'search',
        AnalyticsEvent.payload.ilike(f'%{crop}%')
    ).count()

    views = AnalyticsEvent.query.filter(
        AnalyticsEvent.event_type == 'view',
        AnalyticsEvent.payload.ilike(f'%{crop}%')
    ).count()

    cart_adds = AnalyticsEvent.query.filter(
        AnalyticsEvent.event_type == 'cart_add',
        AnalyticsEvent.payload.ilike(f'%{crop}%')
    ).count()

    # Completed orders
    all_products_of_crop = Product.query.filter(Product.name.ilike(f'%{crop}%')).all()
    prod_ids = [p.id for p in all_products_of_crop]
    orders = Order.query.filter(
        Order.product_id.in_(prod_ids),
        Order.status.in_(['Completed', 'COMPLETED', 'Delivered', 'DELIVERED'])
    ).count() if prod_ids else 0

    # Baseline demand counts based on Kerala crops
    crop_lower = crop.lower()
    base_searches = 0
    base_cart_adds = 0
    base_orders = 0

    if "tomato" in crop_lower:
        base_searches, base_cart_adds, base_orders = 12, 5, 2
    elif "coconut" in crop_lower:
        base_searches, base_cart_adds, base_orders = 15, 8, 3
    elif "rice" in crop_lower or "paddy" in crop_lower:
        base_searches, base_cart_adds, base_orders = 5, 2, 1
    elif "banana" in crop_lower:
        base_searches, base_cart_adds, base_orders = 8, 4, 2
    else:
        base_searches, base_cart_adds, base_orders = 2, 1, 0

    # Combined demand score
    eff_searches = searches + base_searches
    eff_cart_adds = cart_adds + base_cart_adds
    eff_orders = orders + base_orders
    eff_views = views + (base_searches * 2)

    demand_score = eff_searches * 1 + eff_views * 2 + eff_cart_adds * 3 + eff_orders * 5

    if demand_score < 25:
        demand = "Low"
    elif demand_score < 60:
        demand = "Medium"
    else:
        demand = "High"

    # Confidence logic based on real user actions
    total_real_records = searches + views + cart_adds + orders + count
    if total_real_records < 15:
        confidence = "Low (As Real Data Grows)"
        source_label = "Baseline + Activity"
        analytics_details = {
            "searches": eff_searches,
            "cart_adds": eff_cart_adds,
            "orders": eff_orders
        }
    else:
        confidence = "High"
        source_label = "Naadan Marketplace Data"
        analytics_details = {
            "searches": searches,
            "cart_adds": cart_adds,
            "orders": orders
        }

    # 5. Opportunity Suggestion
    if demand == "High" and competition == "Low":
        opportunity = f"Only {count} farmers are currently selling {crop} in your area. Demand is higher than supply. Excellent time to list!"
    elif competition == "High":
        opportunity = f"{crop} listings are currently saturated. Consider competitive pricing or selling in bundles."
    else:
        opportunity = f"Market conditions for {crop} are stable. Keep pricing close to the median of ₹{int(median_price)}/kg for steady sales."

    import datetime
    return jsonify({
        "crop": crop,
        "median_price": round(median_price, 1),
        "min_price": round(min_price, 1),
        "max_price": round(max_price, 1),
        "listings_count": count,
        "total_quantity": round(total_qty, 1),
        "competition": competition,
        "demand": demand,
        "confidence": confidence,
        "source": source_label,
        "analytics_details": analytics_details,
        "opportunity": opportunity,
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %I:%M %p")
    }), 200

@market_bp.route('/performance', methods=['GET'])
@firebase_required()
def get_farmer_performance(current_user):
    if not current_user.is_farmer:
        return jsonify({"msg": "Forbidden. Farmer capability required."}), 403
    from models import Order, Rating
    
    # Orders for this farmer
    orders = Order.query.filter_by(farmer_id=current_user.id).all()
    completed_orders = [o for o in orders if o.status in ['Completed', 'COMPLETED', 'Delivered', 'DELIVERED']]
    
    total_sales = sum(o.total_price for o in completed_orders)
    
    # Monthly revenue (last 30 days)
    import datetime
    thirty_days_ago = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    monthly_orders = [o for o in completed_orders if o.created_at >= thirty_days_ago]
    monthly_revenue = sum(o.total_price for o in monthly_orders)
    
    # Ratings
    ratings = Rating.query.filter_by(farmer_id=current_user.id).all()
    avg_rating = round(sum(r.rating for r in ratings) / len(ratings), 1) if ratings else None
    total_ratings = len(ratings)
    
    # Satisfaction rate (% of 4 or 5 stars)
    if ratings:
        satisfied = sum(1 for r in ratings if r.rating >= 4)
        satisfaction = int((satisfied / len(ratings)) * 100)
    else:
        satisfaction = None
        
    # Repeat buyers rate
    buyer_order_counts = {}
    for o in orders:
        buyer_order_counts[o.buyer_id] = buyer_order_counts.get(o.buyer_id, 0) + 1
    
    repeat_buyers_count = sum(1 for bid, cnt in buyer_order_counts.items() if cnt >= 2)
    
    from models import Product
    # Calculate additional inventory and sales analytics
    total_qty_sold = sum(o.quantity_ordered for o in completed_orders)
    
    # Monthly sales volume (last 30 days completed orders quantity)
    monthly_qty_sold = sum(o.quantity_ordered for o in monthly_orders)
    
    # Remaining inventory (available stock across active farmer products)
    farmer_products = Product.query.filter_by(farmer_id=current_user.id).all()
    remaining_inventory = sum(p.available_stock for p in farmer_products)
    
    # Top Performing Crops (group by product name from completed orders)
    crop_sales = {}
    for o in completed_orders:
        prod = Product.query.get(o.product_id)
        prod_name = prod.name if prod else "Unknown"
        crop_sales[prod_name] = crop_sales.get(prod_name, 0) + o.quantity_ordered
    
    sorted_crops = sorted(crop_sales.items(), key=lambda x: x[1], reverse=True)
    most_purchased_crops = [{"crop": name, "quantity": qty} for name, qty in sorted_crops[:5]]

    reserved_inventory = sum(p.reserved_stock for p in farmer_products)
    most_popular_product = sorted_crops[0][0] if sorted_crops else "None"
    
    return jsonify({
        "total_sales": round(total_sales, 1),
        "monthly_revenue": round(monthly_revenue, 1),
        "average_rating": avg_rating,
        "total_ratings": total_ratings,
        "satisfaction": satisfaction,
        "repeat_buyers": repeat_buyers_count,
        "response_speed": current_user.response_speed or "Normal",
        "total_qty_sold": total_qty_sold,
        "remaining_inventory": remaining_inventory,
        "reserved_inventory": reserved_inventory,
        "most_popular_product": most_popular_product,
        "monthly_qty_sold": monthly_qty_sold,
        "most_purchased_crops": most_purchased_crops,
        "revenue_generated": round(total_sales, 1)
    }), 200
