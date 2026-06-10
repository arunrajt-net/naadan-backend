from flask import Blueprint, request, jsonify
from models import db, User, Product, LocationAudit, AuditEvent, PasswordReset, SMSAuditLog, RegistrationOTP
from datetime import datetime, timedelta
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from auth_middleware import decode_jwt_payload_offline, firebase_required
import math

def calculate_haversine(lat1, lon1, lat2, lon2):
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 0.0
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

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
            
        import os
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@naadan.com").strip()
        admin_phone = os.environ.get("ADMIN_PHONE", "9497856550").strip()
        is_admin_user = (email == admin_email or phone == admin_phone or uid == "admin-uid")

        if role == 'admin' and not is_admin_user:
            return jsonify({"msg": "Forbidden. Admin role is restricted."}), 403

        if is_admin_user:
            role = 'admin'

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
                
        # 3. If not found by UID or email, search by phone to detect duplicates (especially for Phone/Admin Auth users)
        if not user and phone:
            clean_phone = phone[-10:] if len(phone) >= 10 else phone
            user = User.query.filter(User.phone.like(f"%{clean_phone}")).first()
            if user:
                print(f"[DEBUG_SYNC] Auto-linking existing account (ID: {user.id}) to new Firebase UID: {uid}")
                user.firebase_uid = uid
                db.session.commit()
        
        if user:
            # Safely migrate fields for backward compatibility
            if not user.is_farmer and not user.is_buyer and not user.is_admin:
                if user.role == 'farmer':
                    user.is_farmer = True
                elif user.role == 'admin':
                    user.is_admin = True
                else:
                    user.is_buyer = True

            if is_admin_user:
                user.is_admin = True
                user.is_farmer = True
                user.is_buyer = True
                user.role = 'admin'

            # Update name safely: only overwrite if a custom name is explicitly passed
            if 'name' in data and data.get('name') and data.get('name') not in ['Anonymous', 'User']:
                user.name = data.get('name')
            elif not user.name or user.name in ['Anonymous', 'User']:
                if name and name != 'Anonymous' and name != 'User':
                    user.name = name

            if lat is not None or lng is not None:
                if lat is None or lng is None:
                    return jsonify({"error": "Latitude and longitude must both be provided."}), 400
                try:
                    lat_f = float(lat)
                    lng_f = float(lng)
                except (ValueError, TypeError):
                    return jsonify({"error": "Invalid coordinate formats."}), 400
                if lat_f == 0.0 or lng_f == 0.0:
                    return jsonify({"error": "Invalid coordinates [0,0] provided."}), 400
                if lat_f < -90.0 or lat_f > 90.0 or lng_f < -180.0 or lng_f > 180.0:
                    return jsonify({"error": "Coordinates are out of global bounds."}), 400

                gps_acc = data.get('gps_accuracy')
                if gps_acc is not None:
                    try:
                        gps_acc_f = float(gps_acc)
                        if gps_acc_f > 100.0:
                            return jsonify({"error": "GPS accuracy is too low (must be within 100 meters)."}), 400
                    except (ValueError, TypeError):
                        pass

                # Check for active pickup/delivery orders for the farmer
                if user.is_farmer:
                    from models import Order
                    active_order_statuses = ['Accepted', 'Packed', 'Out For Delivery', 'Waiting Customer Confirmation']
                    active_orders_count = Order.query.filter(
                        Order.farmer_id == user.id,
                        Order.status.in_(active_order_statuses)
                    ).count()
                    if active_orders_count > 0 and not data.get('confirm_active_orders', False):
                        return jsonify({
                            "status": "active_orders_warning",
                            "msg": "You currently have active orders. Changing farm location may affect customer navigation. Continue?"
                        }), 200

                # Log location changes to audit log
                if user.lat != lat_f or user.lng != lng_f:
                    dist_km = calculate_haversine(user.lat, user.lng, lat_f, lng_f) if (user.lat is not None and user.lng is not None) else 0.0
                    audit_log = LocationAudit(
                        user_id=user.id,
                        old_lat=user.lat,
                        old_lng=user.lng,
                        new_lat=lat_f,
                        new_lng=lng_f,
                        change_distance_km=dist_km,
                        change_method=data.get('change_method', 'Manual Map Pin')
                    )
                    db.session.add(audit_log)
                    user.location_last_updated = datetime.utcnow()
                    if gps_acc is not None:
                        try:
                            user.gps_accuracy = float(gps_acc)
                        except:
                            pass

                user.lat = lat_f
                user.lng = lng_f
                Product.query.filter_by(farmer_id=user.id).update({
                    Product.lat: lat_f,
                    Product.lng: lng_f
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
            if 'pickup_landmark' in data:
                user.pickup_landmark = data.get('pickup_landmark')
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
                    "pickup_instructions": user.pickup_instructions,
                    "pickup_landmark": user.pickup_landmark
                }
            }), 200
        else:
            # Create new user
            phone_verified_val = False
            if phone:
                clean_phone = phone[-10:] if len(phone) >= 10 else phone
                if clean_phone.isdigit() and len(clean_phone) == 10:
                    registration_token = data.get('registration_token')
                    token_rec = RegistrationOTP.query.filter_by(
                        phone=clean_phone,
                        registration_token=registration_token,
                        is_used=False
                    ).first()
                    if not token_rec or token_rec.token_expires_at < datetime.utcnow():
                        return jsonify({"msg": "Phone number verification is required to create an account."}), 400
                    token_rec.is_used = True
                    phone_verified_val = True

            is_f = False
            is_b = False
            is_a = False
            
            if role == 'farmer':
                is_f = True
            elif role == 'admin':
                # Block self-registration as admin UNLESS they are the configured admin
                if not is_admin_user:
                    return jsonify({"msg": "Admin self-registration is restricted."}), 403
                is_a = True
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
                pickup_instructions=data.get('pickup_instructions'),
                phone_verified=phone_verified_val
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
                    "pickup_instructions": new_user.pickup_instructions,
                    "pickup_landmark": new_user.pickup_landmark
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
                "pickup_instructions": user.pickup_instructions,
                "pickup_landmark": user.pickup_landmark
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
        import os
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@naadan.com").strip()
        admin_phone = os.environ.get("ADMIN_PHONE", "9497856550").strip()
        if current_user.email == admin_email or current_user.phone == admin_phone or current_user.firebase_uid == "admin-uid":
            current_user.is_admin = True
            current_user.is_buyer = True
            current_user.is_farmer = True
            current_user.role = 'admin'
        else:
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
            "is_admin": bool(current_user.is_admin),
            "location_privacy": current_user.location_privacy,
            "pickup_instructions": current_user.pickup_instructions,
            "pickup_landmark": current_user.pickup_landmark
        }
    }), 200


@auth_bp.route('/location-history', methods=['GET'])
@firebase_required()
def get_location_history(current_user):
    logs = LocationAudit.query.filter_by(user_id=current_user.id).order_by(LocationAudit.timestamp.desc()).limit(10).all()
    res = []
    for log in logs:
        res.append({
            "id": log.id,
            "old_lat": log.old_lat,
            "old_lng": log.old_lng,
            "new_lat": log.new_lat,
            "new_lng": log.new_lng,
            "change_distance_km": round(log.change_distance_km or 0.0, 2),
            "change_method": log.change_method,
            "timestamp": log.timestamp.isoformat() if log.timestamp else ""
        })
    return jsonify(res), 200


@auth_bp.route('/admin-login', methods=['POST'])
def admin_login():
    from werkzeug.security import check_password_hash
    import os
    
    data = request.get_json() or {}
    username = data.get('username', '').strip()  # can be email or phone
    password = data.get('password', '').strip()

    admin_email = os.environ.get("ADMIN_EMAIL", "admin@naadan.com").strip()
    admin_phone = os.environ.get("ADMIN_PHONE", "9497856550").strip()
    admin_hash = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()

    if not username or not password:
        return jsonify({"msg": "Username and password are required."}), 400

    # Match either email or phone
    if username != admin_email and username != admin_phone:
        return jsonify({"msg": "Invalid credentials."}), 401

    if not admin_hash or not check_password_hash(admin_hash, password):
        return jsonify({"msg": "Invalid credentials."}), 401

    # Check if admin user exists in DB, otherwise create them
    admin_user = User.query.filter((User.email == admin_email) | (User.phone == admin_phone) | (User.firebase_uid == "admin-uid")).first()
    if not admin_user:
        admin_user = User(
            firebase_uid="admin-uid",
            email=admin_email,
            name="Panchayat Admin",
            role="admin",
            phone=admin_phone,
            lat=10.0,
            lng=76.0,
            is_buyer=True,
            is_farmer=True,
            is_admin=True,
            is_verified=True,
            verification_status="VERIFIED"
        )
        db.session.add(admin_user)
        db.session.commit()
        log_audit_event(admin_user.id, "Admin Created", "Auto-created admin user record on successful login")
    else:
        # Ensure correct roles are set
        admin_user.is_admin = True
        admin_user.is_buyer = True
        admin_user.is_farmer = True
        admin_user.role = "admin"
        admin_user.firebase_uid = "admin-uid"  # Align UID
        db.session.commit()

    log_audit_event(admin_user.id, "Admin Login", "Successful admin session login")

    # Return details including the custom admin token
    return jsonify({
        "msg": "Admin logged in successfully",
        "token": "admin-session-token",
        "user": {
            "id": admin_user.id,
            "name": admin_user.name,
            "email": admin_user.email,
            "role": "admin",
            "phone": admin_user.phone,
            "lat": admin_user.lat,
            "lng": admin_user.lng,
            "delivery_available": False,
            "delivery_price_per_km": 0.0,
            "is_verified": True,
            "verification_status": "VERIFIED",
            "upi_id": "",
            "farm_name": "Panchayat Admin",
            "is_admin": True,
            "location_privacy": "public"
        }
    }), 200


# ============================================================
# PASSWORD RECOVERY FLOW FOR PHONE USERS
# ============================================================
import secrets
import re
import random
from werkzeug.security import generate_password_hash, check_password_hash

def send_otp_sms(phone, otp, event_type="OTP_RECOVERY", user_id=None):
    from sms_provider import get_sms_provider
    import os
    
    clean_phone = phone[-10:] if len(phone) >= 10 else phone
    msg_text = f"Your Naadan verification code is {otp}. It is valid for 5 minutes."
    
    # Check if there is specific template configured
    if event_type == "OTP_RECOVERY":
        template_id = os.environ.get("MSG91_TEMPLATE_ID", "").strip()
    elif event_type == "OTP_SIGNUP":
        template_id = os.environ.get("MSG91_REGISTER_TEMPLATE_ID", "").strip()
    else:
        template_id = None
        
    return get_sms_provider().send_sms(
        phone=clean_phone,
        event_type=event_type,
        message_text=msg_text,
        template_id=template_id,
        params={"otp": otp},
        user_id=user_id
    )


def run_password_reset_cleanup():
    try:
        now = datetime.utcnow()
        # 1. Expired unused OTP records (older than 5 minutes since expiry)
        expired_unused = PasswordReset.query.filter(
            PasswordReset.otp_expires_at < now,
            PasswordReset.is_used == False
        ).all()
        for r in expired_unused:
            db.session.delete(r)
            
        # 2. Used reset records older than 24 hours
        limit_24h = now - timedelta(hours=24)
        expired_used = PasswordReset.query.filter(
            PasswordReset.is_used == True,
            PasswordReset.created_at < limit_24h
        ).all()
        for r in expired_used:
            db.session.delete(r)
            
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[CLEANUP ERROR] Failed to run password reset cleanup: {e}")


def run_registration_otp_cleanup():
    try:
        now = datetime.utcnow()
        expired_unused = RegistrationOTP.query.filter(
            RegistrationOTP.otp_expires_at < now,
            RegistrationOTP.is_used == False
        ).all()
        for r in expired_unused:
            db.session.delete(r)
            
        limit_24h = now - timedelta(hours=24)
        expired_used = RegistrationOTP.query.filter(
            RegistrationOTP.is_used == True,
            RegistrationOTP.created_at < limit_24h
        ).all()
        for r in expired_used:
            db.session.delete(r)
            
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[CLEANUP ERROR] Failed to run registration OTP cleanup: {e}")


@auth_bp.route('/register-request-otp', methods=['POST'])
def register_request_otp():
    run_registration_otp_cleanup()
    
    data = request.get_json() or {}
    phone = data.get('phone', '').strip()

    if not phone:
        return jsonify({"msg": "Phone number is required."}), 400

    clean_phone = phone[-10:] if len(phone) >= 10 else phone

    # Indian Phone Validation
    if not re.match(r'^[6-9]\d{9}$', clean_phone):
        return jsonify({"msg": "Please enter a valid 10-digit Indian phone number starting with 6, 7, 8, or 9."}), 400

    # Prevent registration if user already exists
    existing_user = User.query.filter_by(phone=clean_phone).first()
    if existing_user:
        return jsonify({"msg": "An account with this phone number already exists."}), 400

    # 1. OTP Cooldown Check: 60 seconds
    cooldown_limit = datetime.utcnow() - timedelta(seconds=60)
    recent_otp = RegistrationOTP.query.filter(
        RegistrationOTP.phone == clean_phone,
        RegistrationOTP.created_at >= cooldown_limit
    ).first()
    if recent_otp:
        return jsonify({"msg": "Please wait 60 seconds before requesting a new OTP."}), 429

    # 2. Rate Limiting Check: 5/hour, 10/day
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    hourly_requests = RegistrationOTP.query.filter(
        RegistrationOTP.phone == clean_phone,
        RegistrationOTP.created_at >= one_hour_ago
    ).count()
    if hourly_requests >= 5:
        return jsonify({"msg": "Too many OTP requests. Please try again later."}), 429

    one_day_ago = datetime.utcnow() - timedelta(days=1)
    daily_requests = RegistrationOTP.query.filter(
        RegistrationOTP.phone == clean_phone,
        RegistrationOTP.created_at >= one_day_ago
    ).count()
    if daily_requests >= 10:
        return jsonify({"msg": "Too many OTP requests. Please try again tomorrow."}), 429

    # Generate 6-digit OTP
    otp = "".join([str(random.randint(0, 9)) for _ in range(6)])
    otp_hash = generate_password_hash(otp)
    otp_expires_at = datetime.utcnow() + timedelta(minutes=5)
    
    # Store hashed OTP
    reg_record = RegistrationOTP(
        phone=clean_phone,
        otp_hash=otp_hash,
        otp_expires_at=otp_expires_at
    )
    db.session.add(reg_record)
    db.session.commit()
    
    # Send SMS
    success = send_otp_sms(clean_phone, otp, event_type="OTP_SIGNUP")
    if not success:
        return jsonify({"msg": "Failed to send OTP. Please try again."}), 500

    return jsonify({"msg": "Verification OTP sent successfully."}), 200


@auth_bp.route('/register-verify-otp', methods=['POST'])
def register_verify_otp():
    run_registration_otp_cleanup()
    
    data = request.get_json() or {}
    phone = data.get('phone', '').strip()
    otp = data.get('otp', '').strip()

    if not phone or not otp:
        return jsonify({"msg": "Phone and OTP are required."}), 400

    clean_phone = phone[-10:] if len(phone) >= 10 else phone

    now = datetime.utcnow()
    record = RegistrationOTP.query.filter(
        RegistrationOTP.phone == clean_phone,
        RegistrationOTP.otp_expires_at > now,
        RegistrationOTP.is_used == False
    ).order_by(RegistrationOTP.created_at.desc()).first()

    if not record:
        return jsonify({"msg": "Invalid or expired OTP."}), 400

    # Verification lockout limit (5 attempts)
    if record.verification_attempts >= 5:
        return jsonify({"msg": "Too many failed attempts. Please request a new OTP."}), 400

    # Increment attempts
    record.verification_attempts += 1
    db.session.commit()

    if not check_password_hash(record.otp_hash, otp):
        return jsonify({"msg": "Invalid or expired OTP."}), 400

    # Generate a secure single-use registration token
    registration_token = secrets.token_hex(32)
    record.registration_token = registration_token
    record.token_expires_at = datetime.utcnow() + timedelta(minutes=15)
    db.session.commit()

    return jsonify({"registration_token": registration_token}), 200


@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    run_password_reset_cleanup()
    
    data = request.get_json() or {}
    phone = data.get('phone', '').strip()

    if not phone:
        return jsonify({"msg": "Phone number is required."}), 400

    clean_phone = phone[-10:] if len(phone) >= 10 else phone

    # Indian Phone Validation
    if not re.match(r'^[6-9]\d{9}$', clean_phone):
        return jsonify({"msg": "Please enter a valid 10-digit Indian phone number starting with 6, 7, 8, or 9."}), 400

    # 1. OTP Cooldown Check: Prevent repeated generation within 60 seconds
    cooldown_limit = datetime.utcnow() - timedelta(seconds=60)
    recent_otp = PasswordReset.query.filter(
        PasswordReset.phone == clean_phone,
        PasswordReset.created_at >= cooldown_limit
    ).first()
    if recent_otp:
        return jsonify({"msg": "Please wait 60 seconds before requesting a new OTP."}), 429

    # 2. Rate Limiting Check: Maximum 5 OTP requests per hour, 10 per day
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    hourly_requests = PasswordReset.query.filter(
        PasswordReset.phone == clean_phone,
        PasswordReset.created_at >= one_hour_ago
    ).count()
    if hourly_requests >= 5:
        return jsonify({"msg": "Too many password reset requests. Please try again later."}), 429

    one_day_ago = datetime.utcnow() - timedelta(days=1)
    daily_requests = PasswordReset.query.filter(
        PasswordReset.phone == clean_phone,
        PasswordReset.created_at >= one_day_ago
    ).count()
    if daily_requests >= 10:
        return jsonify({"msg": "Too many password reset requests. Please try again tomorrow."}), 429

    # 3. Enumeration Attack Prevention: Check if user exists in DB
    user = User.query.filter_by(phone=clean_phone).first()
    if not user:
        # Return generic success
        return jsonify({"msg": "If the account exists, an OTP has been sent."}), 200

    # Generate 6-digit OTP
    otp = "".join([str(random.randint(0, 9)) for _ in range(6)])
    otp_hash = generate_password_hash(otp)
    otp_expires_at = datetime.utcnow() + timedelta(minutes=5)
    
    # Store hashed OTP
    reset_record = PasswordReset(
        phone=clean_phone,
        otp_hash=otp_hash,
        otp_expires_at=otp_expires_at
    )
    db.session.add(reset_record)
    db.session.commit()
    
    # Audit log entry
    log_audit_event(user.id, "OTP Requested", "OTP requested for password recovery.")

    # Send SMS
    success = send_otp_sms(clean_phone, otp, event_type="OTP_RECOVERY", user_id=user.id)
    if not success:
        return jsonify({"msg": "Failed to send OTP. Please try again."}), 500

    return jsonify({"msg": "If the account exists, an OTP has been sent."}), 200


@auth_bp.route('/verify-recovery-otp', methods=['POST'])
def verify_recovery_otp():
    run_password_reset_cleanup()
    
    data = request.get_json() or {}
    phone = data.get('phone', '').strip()
    otp = data.get('otp', '').strip()

    if not phone or not otp:
        return jsonify({"msg": "Phone and OTP are required."}), 400

    # Find the latest active record
    now = datetime.utcnow()
    record = PasswordReset.query.filter(
        PasswordReset.phone == phone,
        PasswordReset.otp_expires_at > now,
        PasswordReset.is_used == False
    ).order_by(PasswordReset.created_at.desc()).first()

    if not record:
        return jsonify({"msg": "Invalid or expired OTP."}), 400

    # Verification lockout limit (5 attempts)
    if record.verification_attempts >= 5:
        return jsonify({"msg": "Too many failed attempts. Please request a new OTP."}), 400

    # Increment attempts
    record.verification_attempts += 1
    db.session.commit()

    if not check_password_hash(record.otp_hash, otp):
        return jsonify({"msg": "Invalid or expired OTP."}), 400

    # Generate a secure single-use recovery token
    reset_token = secrets.token_hex(32)
    record.reset_token = reset_token
    record.token_expires_at = datetime.utcnow() + timedelta(minutes=15)
    db.session.commit()

    # Log audit event
    user = User.query.filter_by(phone=phone).first()
    if user:
        log_audit_event(user.id, "OTP Verified", "OTP successfully verified for password recovery.")

    return jsonify({"reset_token": reset_token}), 200


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    run_password_reset_cleanup()
    
    data = request.get_json() or {}
    phone = data.get('phone', '').strip()
    reset_token = data.get('reset_token', '').strip()
    new_password = data.get('new_password', '').strip()

    if not phone or not reset_token or not new_password:
        return jsonify({"msg": "Phone, reset token, and new password are required."}), 400

    # Strong Password Validation
    if not re.match(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$', new_password):
        return jsonify({"msg": "Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, and one number."}), 400

    now = datetime.utcnow()
    record = PasswordReset.query.filter_by(phone=phone, reset_token=reset_token, is_used=False).first()
    if not record or record.token_expires_at < now:
        return jsonify({"msg": "Invalid or expired reset token."}), 400

    user = User.query.filter_by(phone=phone).first()
    if not user:
        return jsonify({"msg": "User not found."}), 404

    # Update in Firebase Auth
    import os
    import firebase_admin
    from firebase_admin import auth as admin_auth
    
    firebase_configured = True
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
    except Exception as e:
        print("Firebase Admin SDK initialization skipped/failed, using local dev mock mode:", str(e))
        firebase_configured = False

    # Perform credentials update
    if firebase_configured:
        try:
            dummy_email = f"{phone}@naadan.com"
            try:
                fb_user = admin_auth.get_user_by_email(dummy_email)
            except Exception:
                fb_user = admin_auth.get_user_by_email(user.email)
            
            admin_auth.update_user(fb_user.uid, password=new_password)
            admin_auth.revoke_refresh_tokens(fb_user.uid)
            print(f"[FIREBASE SUCCESS] Password updated for UID: {fb_user.uid} and active sessions revoked.")
        except Exception as fb_err:
            print("[FIREBASE ERROR] Failed to update password via admin SDK:", str(fb_err))
            firebase_configured = False

    if not firebase_configured:
        print(f"\n[DEV MODE - NO FIREBASE ACCOUNT] Password reset simulated successfully for user phone {phone} to: {new_password}\n")

    # Invalidate reset token
    record.is_used = True
    db.session.commit()

    # Log audit event
    log_audit_event(user.id, "Password Reset Complete", "Password updated and active sessions revoked.")

    return jsonify({"msg": "Password updated successfully."}), 200



@auth_bp.route('/reset-password-firebase', methods=['POST'])
def reset_password_firebase():
    """Reset password after Firebase Phone OTP verification (free SMS via Firebase)."""
    import re
    import firebase_admin
    from firebase_admin import auth as admin_auth
    from werkzeug.security import generate_password_hash

    data = request.json or {}
    phone = data.get('phone', '').strip()
    new_password = data.get('new_password', '').strip()
    firebase_id_token = data.get('firebase_id_token', '').strip()

    if not phone or not new_password or not firebase_id_token:
        return jsonify({'msg': 'Missing required fields.'}), 400

    # Validate password strength
    if not re.match(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).{8,}$', new_password):
        return jsonify({'msg': 'Password must be at least 8 characters long and contain at least one uppercase letter, one lowercase letter, and one number.'}), 400

    # Verify Firebase ID token (only needs projectId, works without service account)
    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={'projectId': 'naadan-ebd6e'})
        decoded_token = admin_auth.verify_id_token(firebase_id_token)
        token_phone = decoded_token.get('phone_number', '')  # e.g. '+919497856550'
        expected_phone = '+91' + phone
        if token_phone != expected_phone:
            return jsonify({'msg': 'Phone verification mismatch. Please try again.'}), 400
    except Exception as e:
        print(f"[FIREBASE TOKEN ERROR] {e}")
        return jsonify({'msg': 'OTP verification failed or expired. Please try again.'}), 401

    # Find user in DB by phone number
    user = User.query.filter_by(phone=phone).first()
    if not user:
        return jsonify({'msg': 'No account found with this phone number.'}), 404

    # Update password hash in DB
    user.password_hash = generate_password_hash(new_password)
    db.session.commit()

    # Try to update Firebase password (best-effort)
    try:
        dummy_email = f'{phone}@naadan.com'
        try:
            fb_user = admin_auth.get_user_by_email(dummy_email)
        except Exception:
            fb_user = None
        if fb_user:
            admin_auth.update_user(fb_user.uid, password=new_password)
            admin_auth.revoke_refresh_tokens(fb_user.uid)
            print(f"[FIREBASE] Password updated for {fb_user.uid}")
    except Exception as fb_err:
        print(f"[FIREBASE FALLBACK] Could not update Firebase password: {fb_err}")

    # Audit log
    log_audit_event(user.id, 'Password Reset Complete (Firebase OTP)', f'Password reset via Firebase Phone OTP for {phone}.')

    return jsonify({'msg': 'Password updated successfully.'}), 200
