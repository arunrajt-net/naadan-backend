from flask import Blueprint, request, jsonify
from models import db, User, Order, Rating, AuditEvent
from auth_middleware import firebase_required
import os, json, uuid
from datetime import datetime

verification_bp = Blueprint("verification_bp", __name__)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def log_audit_event(user_id, action, details):
    try:
        event = AuditEvent(user_id=user_id, action=action, details=details)
        db.session.add(event)
        db.session.commit()
    except Exception as e:
        print(f"Failed to log audit event: {e}")

# ----------------------------------------------------------------
# GET /api/verify/status  - Get current farmer verification status
# ----------------------------------------------------------------
@verification_bp.route("/status", methods=["GET"])
@firebase_required()
def get_verification_status(current_user):
    current_user.compute_trust_score()
    db.session.commit()
    return jsonify({
        "phone_verified": current_user.phone_verified or bool(current_user.phone),
        "farm_verified": current_user.farm_verified,
        "farm_verification_status": current_user.farm_verification_status or "NONE",
        "community_verified": current_user.community_verified,
        "community_doc_status": current_user.community_doc_status or "NONE",
        "trust_score": current_user.trust_score or 0,
        "average_rating": round(current_user.average_rating or 0, 1),
        "total_ratings": current_user.total_ratings or 0,
        "completed_orders_count": current_user.completed_orders_count or 0,
        "response_speed": current_user.response_speed or "Normal",
    })

# ----------------------------------------------------------------
# POST /api/verify/phone  - Mark phone as verified (call after OTP success)
# ----------------------------------------------------------------
@verification_bp.route("/phone", methods=["POST"])
@firebase_required()
def verify_phone(current_user):
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    if phone and len(phone) >= 10:
        current_user.phone = phone
    current_user.phone_verified = True
    current_user.compute_trust_score()
    db.session.commit()
    
    log_audit_event(current_user.id, "Phone Verified", f"Verified phone number: {phone}")
    
    return jsonify({"msg": "Phone verified!", "trust_score": current_user.trust_score})

# ----------------------------------------------------------------
# POST /api/verify/farm  - Submit farm photos
# ----------------------------------------------------------------
@verification_bp.route("/farm", methods=["POST"])
@firebase_required()
def submit_farm_verification(current_user):
    # Enforce role capability check (Condition 3)
    if not current_user.is_farmer:
        log_audit_event(current_user.id, "Permission Denied", "Attempted to submit farm verification without is_farmer flag")
        return jsonify({"msg": "Only farmers can submit farm verification"}), 403

    uploaded_files = []
    farmer_upload_dir = os.path.join(UPLOAD_DIR, f"farmer_{current_user.id}")
    os.makedirs(farmer_upload_dir, exist_ok=True)

    for key in ["photo_farm", "photo_crop", "photo_entrance"]:
        file = request.files.get(key)
        if file and file.filename:
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
                return jsonify({"msg": f"Invalid file type for {key}. Use JPG/PNG."}), 400
            fname = f"{key}_{uuid.uuid4().hex[:8]}{ext}"
            fpath = os.path.join(farmer_upload_dir, fname)
            file.save(fpath)
            uploaded_files.append(fname)

    if len(uploaded_files) < 1:
        return jsonify({"msg": "Please upload at least one farm photo"}), 400

    current_user.farm_photos_json = json.dumps(uploaded_files)
    current_user.farm_verification_status = "PENDING"
    # Auto-approve for demo purposes (in production, admin reviews)
    current_user.farm_verified = True
    current_user.farm_verification_status = "VERIFIED"
    current_user.is_verified = True
    current_user.verification_status = "PENDING"
    current_user.compute_trust_score()
    db.session.commit()

    log_audit_event(current_user.id, "Farm Verification Submitted", f"Uploaded {len(uploaded_files)} photos, auto-verified for demo.")

    return jsonify({
        "msg": "Farm photos submitted! Your Farm Verified badge is now active.",
        "farm_verified": True,
        "trust_score": current_user.trust_score,
        "files_uploaded": len(uploaded_files),
    })

# ----------------------------------------------------------------
# POST /api/verify/community  - Submit community document
# ----------------------------------------------------------------
@verification_bp.route("/community", methods=["POST"])
@firebase_required()
def submit_community_verification(current_user):
    # Enforce role capability check (Condition 3)
    if not current_user.is_farmer:
        log_audit_event(current_user.id, "Permission Denied", "Attempted to submit community verification without is_farmer flag")
        return jsonify({"msg": "Only farmers can submit community verification"}), 403

    doc_type = request.form.get("doc_type", "").strip()
    valid_types = ["panchayat_cert", "farmer_card", "agri_dept_cert", "land_tax", "possession_cert"]
    if doc_type not in valid_types:
        return jsonify({"msg": "Invalid document type"}), 400

    file = request.files.get("document")
    if not file or not file.filename:
        return jsonify({"msg": "Please upload a document photo"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".pdf", ".webp"]:
        return jsonify({"msg": "Invalid file type. Use JPG/PNG/PDF."}), 400

    farmer_upload_dir = os.path.join(UPLOAD_DIR, f"farmer_{current_user.id}")
    os.makedirs(farmer_upload_dir, exist_ok=True)
    fname = f"community_doc_{uuid.uuid4().hex[:8]}{ext}"
    fpath = os.path.join(farmer_upload_dir, fname)
    file.save(fpath)

    current_user.community_doc_type = doc_type
    current_user.community_doc_status = "PENDING"
    # Auto-approve for demo (in production: admin reviews)
    current_user.community_verified = True
    current_user.community_doc_status = "VERIFIED"
    current_user.compute_trust_score()
    db.session.commit()

    log_audit_event(current_user.id, "Community Verification Submitted", f"Submitted document type: {doc_type}, auto-verified for demo.")

    return jsonify({
        "msg": "Community document submitted! Your Community Verified badge is now active.",
        "community_verified": True,
        "trust_score": current_user.trust_score,
    })

# ----------------------------------------------------------------
# POST /api/verify/rating  - Buyer rates farmer after completed order
# ----------------------------------------------------------------
@verification_bp.route("/rating", methods=["POST"])
@firebase_required()
def submit_rating(current_user):
    data = request.get_json() or {}
    farmer_id = data.get("farmer_id")
    rating_val = data.get("rating")
    feedback = data.get("feedback", "")
    order_id = data.get("order_id")

    if not farmer_id or not rating_val:
        return jsonify({"msg": "farmer_id and rating are required"}), 400

    try:
        rating_int = max(1, min(int(rating_val), 5))
    except:
        return jsonify({"msg": "Rating must be 1-5"}), 400

    farmer = User.query.get(farmer_id)
    if not farmer:
        return jsonify({"msg": "Farmer not found"}), 404

    # Check if buyer already rated this order
    if order_id:
        from models import Rating as RatingModel
        existing = RatingModel.query.filter_by(order_id=order_id, buyer_id=current_user.id).first()
        if existing:
            return jsonify({"msg": "You have already rated this order"}), 400

    from models import Rating as RatingModel
    new_rating = RatingModel(
        farmer_id=farmer_id,
        buyer_id=current_user.id,
        order_id=order_id,
        rating=rating_int,
        feedback=feedback[:500] if feedback else "",
    )
    db.session.add(new_rating)

    # Recalculate farmer average rating
    all_ratings = RatingModel.query.filter_by(farmer_id=farmer_id).all()
    total = sum(r.rating for r in all_ratings) + rating_int
    count = len(all_ratings) + 1
    farmer.average_rating = round(total / count, 1)
    farmer.total_ratings = count
    farmer.compute_trust_score()

    db.session.commit()
    
    log_audit_event(current_user.id, "Farmer Rated", f"Rated farmer {farmer_id} with score {rating_int}")
    
    return jsonify({
        "msg": "Rating submitted!",
        "new_average": farmer.average_rating,
        "total_ratings": farmer.total_ratings,
        "farmer_trust_score": farmer.trust_score,
    })

# ----------------------------------------------------------------
# GET /api/verify/farmer/<id>  - Get farmer public trust profile
# ----------------------------------------------------------------
@verification_bp.route("/farmer/<int:farmer_id>", methods=["GET"])
def get_farmer_trust_profile(farmer_id):
    farmer = User.query.get(farmer_id)
    if not farmer or not farmer.is_farmer:
        return jsonify({"msg": "Farmer not found"}), 404
    farmer.compute_trust_score()
    db.session.commit()
    return jsonify(farmer.to_public_dict())
