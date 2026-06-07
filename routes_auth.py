from flask import Blueprint, request, jsonify
from models import db, User, Product, AuditEvent
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from auth_middleware import decode_jwt_payload_offline, firebase_required
from datetime import datetime

auth_bp = Blueprint('auth_bp', __name__)

def log_audit_event(user_id, action, details):
    try:
        event = AuditEvent(user_id=user_id, action=action, details=details)
        db.session.add(event)
        db.session.commit()
    except Exception as e:
        print(f"Failed to log audit event: {e}")

@auth_bp.route('/sync', methods=['POST'])
def sync_user():
    """
    Syncs a Firebase user into our SQLite DB. This should be called immediately after successful Firebase Login.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"msg": "Missing Authorization header"}), 401
    
    token = auth_header.split(" ")[1]
    
    try:
        if token.startswith("dummy-") or token == "dummy-token":
            # Local Dev/Offline Bypass
            uid = "dummy-uid-12345"
            decoded_token = {
                "user_id": uid,
                "email": "dummy@naadan.com",
                "name": "Dummy User",
                "phone_number": "9497856550"
            }
        else:
            try:
                decoded_token = id_token.verify_firebase_token(token, google_requests.Request(), audience='naadan-ebd6e')
                uid = decoded_token['user_id']
            except Exception as verify_err:
                print("Sync verification failed, attempting offline decode:", str(verify_err))
                decoded = decode_jwt_payload_offline(token)
                if decoded and 'user_id' in decoded:
                    decoded_token = decoded
                    uid = decoded['user_id']
                else:
                    raise verify_err
        email = decoded_token.get('email', '')
        
        data = request.get_json() or {}
        name = data.get('name') or decoded_token.get('name') or 'Anonymous'
        role = data.get('role') or 'buyer'
        lat = data.get('lat')
        lng = data.get('lng')
        phone = data.get('phone') or decoded_token.get('phone_number') or ''
        is_signup = data.get('is_signup', False)
        
        if not phone and email and '@naadan.com' in email:
            phone = email.split('@')[0]
            
        print(f"[DEBUG_SYNC] UID: {uid}, Email: {email}, Role Input: {role}, Is Signup: {is_signup}")
        
        # 1. Search by firebase_uid
        user = User.query.filter_by(firebase_uid=uid).first()
        
        # 2. If not found by UID, search by email to detect duplicates
        if not user and email:
            user = User.query.filter_by(email=email).first()
            if user:
                # SAFE LINKING CONFLICT DETECTED: Returning link required status to frontend
                print(f"[DEBUG_SYNC] Conflict: Email {email} exists under UID {user.firebase_uid} but token has UID {uid}.")
                return jsonify({
                    "status": "link_required",
                    "email": email,
                    "msg": "An account with this email already exists."
                }), 200
        
        if user:
            # Safely migrate fields for backward compatibility
            if not user.is_farmer and not user.is_buyer and not user.is_admin:
                if user.role == 'farmer':
                    user.is_farmer = True
                elif user.role == 'admin':
                    user.is_admin = True
                else:
                    user.is_buyer = True

            # Update name safely: only overwrite if a custom name is explicitly passed
            if 'name' in data and data.get('name') and data.get('name') not in ['Anonymous', 'User']:
                user.name = data.get('name')
            elif not user.name or user.name in ['Anonymous', 'User']:
                if name and name != 'Anonymous' and name != 'User':
                    user.name = name

            if lat is not None and lng is not None:
                is_default_kochi = (lat == 10.0 and lng == 76.0)
                user_has_custom = (user.lat is not None and user.lng is not None and user.lat != 10.0 and user.lng != 76.0)
                if not (is_default_kochi and user_has_custom):
                    user.lat = lat
                    user.lng = lng
                    Product.query.filter_by(farmer_id=user.id).update({
                        Product.lat: lat,
                        Product.lng: lng
                    })
            
            # Update phone safely: only overwrite if explicitly passed or if current phone is unset
            if 'phone' in data and data.get('phone'):
                user.phone = data.get('phone')
            elif not user.phone:
                if phone:
                    user.phone = phone
            deliv_avail = user.delivery_available
            if 'delivery_available' in data:
                deliv_avail = bool(data.get('delivery_available'))
            
            deliv_price = user.delivery_price_per_km
            if 'delivery_price_per_km' in data:
                deliv_price = float(data.get('delivery_price_per_km'))

            if not deliv_avail:
                if deliv_price != 0.0:
                    if 'delivery_price_per_km' in data and data.get('delivery_price_per_km') != 0:
                        return jsonify({"error": "Cannot set a non-zero delivery charge when delivery is unavailable (Pickup Only)."}), 400
                deliv_price = 0.0

            user.delivery_available = deliv_avail
            user.delivery_price_per_km = deliv_price
            if 'upi_id' in data and data.get('upi_id'):
                user.upi_id = data.get('upi_id').strip()
            if 'farm_name' in data and data.get('farm_name'):
                user.farm_name = data.get('farm_name').strip()
            if 'location_privacy' in data:
                user.location_privacy = data.get('location_privacy')
            if 'pickup_instructions' in data:
                user.pickup_instructions = data.get('pickup_instructions')
            db.session.commit()
            
            log_audit_event(user.id, "User Login Sync", f"Synced profile details for user {user.id}")
            
            return jsonify({
                "msg": "User synced",
                "status": "exists",
                "user": {
                    "id": user.id,
                    "name": user.name, 
                    "email": user.email,
                    "role": user.role, 
                    "phone": user.phone, 
                    "lat": user.lat, 
                    "lng": user.lng,
                    "delivery_available": bool(user.delivery_available) if user.delivery_available is not None else False,
                    "delivery_price_per_km": float(user.delivery_price_per_km or 0.0),
                    "is_verified": bool(user.is_verified),
                    "verification_status": user.verification_status,
                    "upi_id": user.upi_id or (user.phone + "@upi" if user.phone else ""),
                    "farm_name": user.farm_name or user.name,
                    "aadhaar_number": user.aadhaar_number,
                    "panchayat_id": user.panchayat_id,
                    "is_buyer": bool(user.is_buyer),
                    "is_farmer": bool(user.is_farmer),
                    "is_admin": bool(user.is_admin),
                    "location_privacy": user.location_privacy,
                    "pickup_instructions": user.pickup_instructions
                }
            }), 200
        else:
            # Create new user
            is_f = False
            is_b = False
            is_a = False
            
            if role == 'farmer':
                is_f = True
            elif role == 'admin':
                # Block self-registration as admin
                return jsonify({"msg": "Admin self-registration is restricted."}), 403
            else:
                is_b = True
                
            is_farmer_role = (role == 'farmer')
            deliv_avail = False if is_farmer_role else bool(data.get('delivery_available', False))
            deliv_price = 0.0 if is_farmer_role else float(data.get('delivery_price_per_km', 0.0))
            
            if not deliv_avail:
                if deliv_price != 0.0:
                    if 'delivery_price_per_km' in data and data.get('delivery_price_per_km') != 0:
                        return jsonify({"error": "Cannot set a non-zero delivery charge when delivery is unavailable (Pickup Only)."}), 400
                deliv_price = 0.0

            new_user = User(
                firebase_uid=uid,
                email=email,
                name=name,
                role=role or 'buyer',
                phone=phone,
                lat=lat if lat is not None else 10.0,
                lng=lng if lng is not None else 76.0,
                delivery_available=deliv_avail,
                delivery_price_per_km=deliv_price,
                is_verified=False,
                verification_status='NONE',
                is_buyer=is_b,
                is_farmer=is_f,
                is_admin=is_a,
                location_privacy=data.get('location_privacy', 'public'),
                pickup_instructions=data.get('pickup_instructions')
            )
            db.session.add(new_user)
            db.session.commit()
            
            log_audit_event(new_user.id, "User Registered", f"Created new profile with primary role {role}")
            
            return jsonify({
                "msg": "User synced and created",
                "status": "created",
                "user": {
                    "id": new_user.id,
                    "name": new_user.name, 
                    "email": new_user.email,
                    "role": new_user.role, 
                    "phone": new_user.phone, 
                    "lat": new_user.lat, 
                    "lng": new_user.lng,
                    "delivery_available": bool(new_user.delivery_available) if new_user.delivery_available is not None else False,
                    "delivery_price_per_km": float(new_user.delivery_price_per_km or 0.0),
                    "is_verified": False,
                    "verification_status": 'NONE',
                    "is_buyer": bool(new_user.is_buyer),
                    "is_farmer": bool(new_user.is_farmer),
                    "is_admin": bool(new_user.is_admin),
                    "location_privacy": new_user.location_privacy,
                    "pickup_instructions": new_user.pickup_instructions
                }
            }), 201

    except Exception as e:
        import traceback
        print("Sync Error Traceback:")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 400

@auth_bp.route('/link-accounts', methods=['POST'])
def link_accounts():
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"msg": "Missing Authorization header"}), 401
    
    token = auth_header.split(" ")[1]
    
    try:
        if token.startswith("dummy-") or token == "dummy-token":
            uid = "dummy-uid-12345"
            decoded_token = {
                "user_id": uid,
                "email": "dummy@naadan.com",
            }
        else:
            try:
                decoded_token = id_token.verify_firebase_token(token, google_requests.Request(), audience='naadan-ebd6e')
                uid = decoded_token['user_id']
            except Exception as verify_err:
                decoded = decode_jwt_payload_offline(token)
                if decoded and 'user_id' in decoded:
                    decoded_token = decoded
                    uid = decoded['user_id']
                else:
                    raise verify_err
        
        email = decoded_token.get('email', '')
        if not email:
            return jsonify({"msg": "Email not found in token"}), 400
            
        user = User.query.filter_by(email=email).first()
        if not user:
            return jsonify({"msg": "No existing account found with this email to link."}), 404
            
        # Link Google UID
        old_uid = user.firebase_uid
        user.firebase_uid = uid
        db.session.commit()
        
        log_audit_event(user.id, "Google Account Linked", f"Linked Firebase UID from {old_uid} to {uid}")
        
        return jsonify({
            "msg": "Accounts linked successfully!",
            "user": {
                "id": user.id,
                "name": user.name, 
                "email": user.email,
                "role": user.role, 
                "phone": user.phone, 
                "lat": user.lat, 
                "lng": user.lng,
                "delivery_available": bool(user.delivery_available) if user.delivery_available is not None else False,
                "delivery_price_per_km": float(user.delivery_price_per_km or 0.0),
                "is_verified": bool(user.is_verified),
                "verification_status": user.verification_status,
                "upi_id": user.upi_id or (user.phone + "@upi" if user.phone else ""),
                "farm_name": user.farm_name or user.name,
                "aadhaar_number": user.aadhaar_number,
                "panchayat_id": user.panchayat_id,
                "is_buyer": bool(user.is_buyer),
                "is_farmer": bool(user.is_farmer),
                "is_admin": bool(user.is_admin),
                "location_privacy": user.location_privacy,
                "pickup_instructions": user.pickup_instructions
            }
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@auth_bp.route('/enable-role', methods=['POST'])
@firebase_required()
def enable_role(current_user):
    data = request.get_json() or {}
    role_to_enable = data.get('role')
    
    print(f"[DEBUG_ENABLE_ROLE] Enabling {role_to_enable} for user {current_user.id}")
    if role_to_enable == 'buyer':
        current_user.is_buyer = True
    elif role_to_enable == 'farmer':
        current_user.is_farmer = True
        if not current_user.role or current_user.role == 'buyer':
            current_user.role = 'farmer'
        current_user.delivery_available = False
        current_user.delivery_price_per_km = 0.0
    elif role_to_enable == 'admin':
        # Strictly reject self-promotion to admin!
        return jsonify({"msg": "Forbidden. Admin capability can only be granted directly by backend."}), 403
    else:
        return jsonify({"msg": "Invalid role"}), 400
        
    db.session.commit()
    log_audit_event(current_user.id, "Role Activated", f"Enabled capability: {role_to_enable}")
    
    return jsonify({
        "msg": f"Role {role_to_enable} enabled",
        "user": {
            "id": current_user.id,
            "name": current_user.name, 
            "email": current_user.email,
            "role": current_user.role, 
            "phone": current_user.phone, 
            "lat": current_user.lat, 
            "lng": current_user.lng,
            "delivery_available": bool(current_user.delivery_available) if current_user.delivery_available is not None else False,
            "delivery_price_per_km": float(current_user.delivery_price_per_km or 0.0),
            "is_verified": bool(current_user.is_verified),
            "verification_status": current_user.verification_status,
            "upi_id": current_user.upi_id or (current_user.phone + "@upi" if current_user.phone else ""),
            "farm_name": current_user.farm_name or current_user.name,
            "aadhaar_number": current_user.aadhaar_number,
            "panchayat_id": current_user.panchayat_id,
            "is_farmer": bool(current_user.is_farmer),
            "is_buyer": bool(current_user.is_buyer),
            "is_admin": bool(current_user.is_admin)
        }
    }), 200
