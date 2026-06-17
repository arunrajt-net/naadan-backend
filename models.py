from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import validates
from datetime import datetime
import json

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    firebase_uid = db.Column(db.String(100), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    role = db.Column(db.String(20), nullable=False)  # farmer, buyer, admin
    is_buyer = db.Column(db.Boolean, default=False, nullable=False)
    is_farmer = db.Column(db.Boolean, default=False, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    __table_args__ = (
        db.CheckConstraint('delivery_available OR delivery_price_per_km = 0.0', name='check_delivery_price_when_unavailable'),
    )

    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    delivery_available = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    delivery_price_per_km = db.Column(db.Float, default=0.0, nullable=False, server_default='0.0')
    upi_id = db.Column(db.String(100), nullable=True)
    farm_name = db.Column(db.String(100), nullable=True)
    location_privacy = db.Column(db.String(20), default="public", nullable=False, server_default="public")
    pickup_instructions = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    gps_accuracy = db.Column(db.Float, nullable=True)
    location_last_updated = db.Column(db.DateTime, nullable=True)
    pickup_landmark = db.Column(db.String(150), nullable=True)
    payment_methods = db.Column(db.String(20), default=None, nullable=True)

    # ---- OLD FIELDS (kept for backward compat) ----
    is_verified = db.Column(db.Boolean, default=False)
    verification_status = db.Column(db.String(20), default="NONE")
    aadhaar_number = db.Column(db.String(20), nullable=True)
    panchayat_id = db.Column(db.String(50), nullable=True)

    # ---- NEW VERIFICATION FIELDS ----
    # Level 1: Mobile Verified (auto-set when phone is confirmed)
    phone_verified = db.Column(db.Boolean, default=False)

    # Level 2: Farm Verified (3 farm photos uploaded, pending admin review)
    farm_verified = db.Column(db.Boolean, default=False)
    farm_photos_json = db.Column(db.Text, nullable=True)     # JSON array of filenames
    farm_verification_status = db.Column(db.String(20), default="NONE")  # NONE, PENDING, VERIFIED

    # Level 3: Community Verified (doc uploaded, pending admin review)
    community_verified = db.Column(db.Boolean, default=False)
    community_doc_type = db.Column(db.String(50), nullable=True)   # panchayat, farmer_card, etc.
    community_doc_status = db.Column(db.String(20), default="NONE")  # NONE, PENDING, VERIFIED

    # Trust Score & Reputation
    trust_score = db.Column(db.Float, default=0.0)
    average_rating = db.Column(db.Float, default=0.0)
    total_ratings = db.Column(db.Integer, default=0)
    completed_orders_count = db.Column(db.Integer, default=0)
    response_speed = db.Column(db.String(20), default="Normal")  # Fast, Normal, Slow

    @validates('delivery_available')
    def validate_delivery_available(self, key, value):
        val_bool = bool(value)
        if not val_bool:
            self.delivery_price_per_km = 0.0
        return val_bool

    @validates('delivery_price_per_km')
    def validate_delivery_price(self, key, value):
        val_float = float(value or 0.0)
        if not self.delivery_available and val_float != 0.0:
            # Enforce automatically forcing to 0.0 or raise validation error
            # If the user tries to save an invalid configuration, we raise a ValueError to prevent it
            raise ValueError("Cannot set a non-zero delivery charge when delivery is unavailable (Pickup Only).")
        return val_float

    @property
    def farm_photos(self):
        try:
            return json.loads(self.farm_photos_json or "[]")
        except:
            return []

    def compute_trust_score(self):
        """Compute trust score out of 100."""
        score = 0.0
        # Level 1: Mobile verified - 20 pts
        if self.phone_verified or (self.phone and len(self.phone) >= 10):
            score += 20
        # Level 2: Farm verified - 25 pts
        if self.farm_verified:
            score += 25
        elif self.farm_verification_status == "PENDING":
            score += 5  # Partial credit for pending
        # Level 3: Community verified - 20 pts
        if self.community_verified:
            score += 20
        elif self.community_doc_status == "PENDING":
            score += 5  # Partial credit
        # Completed orders - up to 15 pts (1 per order, max 15)
        score += min(self.completed_orders_count or 0, 15)
        # Average rating - up to 10 pts (rating * 2)
        if self.average_rating and self.total_ratings and self.total_ratings > 0:
            score += min(self.average_rating * 2, 10)
        # Response speed - up to 5 pts
        speed_pts = {"Fast": 5, "Normal": 3, "Slow": 1}
        score += speed_pts.get(self.response_speed or "Normal", 3)
        # UPI set - 5 pts (commitment to platform)
        if self.upi_id:
            score += 5
        self.trust_score = round(min(score, 100), 1)
        return self.trust_score

    def to_public_dict(self):
        """Safe public data for buyers to see."""
        return {
            "id": self.id,
            "name": self.name,
            "farm_name": self.farm_name,
            "phone": None,
            "upi_id": None,
            "lat": self.lat,
            "lng": self.lng,
            "role": self.role,
            "delivery_available": bool(self.delivery_available) if self.delivery_available is not None else False,
            "delivery_price_per_km": float(self.delivery_price_per_km or 0.0),
            # Verification
            "phone_verified": self.phone_verified or bool(self.phone),
            "farm_verified": self.farm_verified,
            "community_verified": self.community_verified,
            "farm_verification_status": self.farm_verification_status or "NONE",
            "community_doc_status": self.community_doc_status or "NONE",
            "trust_score": self.trust_score or 0,
            "average_rating": round(self.average_rating or 0, 1),
            "total_ratings": self.total_ratings or 0,
            "completed_orders_count": self.completed_orders_count or 0,
            "response_speed": self.response_speed or "Normal",
            "is_verified": self.is_verified,
            "is_buyer": self.is_buyer,
            "is_farmer": self.is_farmer,
            "is_admin": self.is_admin,
            "gps_accuracy": self.gps_accuracy,
            "pickup_landmark": self.pickup_landmark,
            "payment_methods": self.payment_methods
        }

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Float, nullable=False)
    quantity = db.Column(db.String(50), nullable=False)
    image_url = db.Column(db.String(255), nullable=True)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    is_available = db.Column(db.Boolean, default=True)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    delivery_type = db.Column(db.String(20), default="both")  # pickup, delivery, both
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reserved_quantity = db.Column(db.Float, default=0.0)
    idempotency_key = db.Column(db.String(100), unique=True, nullable=True)

    # ---- INVENTORY PROPERTIES ----
    def parse_quantity_str(self):
        import re
        qty_str = str(self.quantity or "").strip().lower()
        match = re.match(r'^([\d\.]+)\s*(.*)$', qty_str)
        if match:
            try:
                val = float(match.group(1))
                if val.is_integer():
                    val = int(val)
                unit = match.group(2).strip() or 'kg'
                return val, unit
            except:
                pass
        return 0.0, 'kg'

    @property
    def total_stock(self):
        val, _ = self.parse_quantity_str()
        return val

    @property
    def unit(self):
        _, unit = self.parse_quantity_str()
        return unit

    @property
    def reserved_stock(self):
        return self.reserved_quantity or 0.0

    @property
    def available_stock(self):
        return max(0.0, self.total_stock - self.reserved_stock)

    @property
    def sold_quantity(self):
        from models import Order
        from datetime import datetime
        now = datetime.utcnow()
        completed_orders = Order.query.filter(
            Order.product_id == self.id,
            Order.status.in_(['Completed', 'COMPLETED']),
            Order.created_at >= datetime(now.year, now.month, 1)
        ).all()
        return sum(o.quantity_ordered for o in completed_orders)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity_ordered = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(40), default="Pending Payment")
    payment_status = db.Column(db.String(20), default="PENDING_PAYMENT")
    payment_method = db.Column(db.String(20), default="UPI")
    upi_ref = db.Column(db.String(100), nullable=True)
    payment_screenshot_url = db.Column(db.String(255), nullable=True)
    utr_number = db.Column(db.String(100), nullable=True)
    payment_verified_at = db.Column(db.DateTime, nullable=True)
    payment_verified_by = db.Column(db.String(100), nullable=True)
    payment_rejection_reason = db.Column(db.String(255), nullable=True)
    delivery_type = db.Column(db.String(20), nullable=False)
    shipping_phone = db.Column(db.String(20), nullable=True)
    shipping_address = db.Column(db.Text, nullable=True)
    delivery_vehicle = db.Column(db.String(20), default="motorcycle")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    idempotency_key = db.Column(db.String(100), unique=True, nullable=True)

    # New order tracking columns for production lifecycle
    delivered_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    completed_by = db.Column(db.String(20), nullable=True)  # 'customer', 'system'
    completion_reason = db.Column(db.String(255), nullable=True)

    # Location Snapshots
    farmer_lat = db.Column(db.Float, nullable=True)
    farmer_lng = db.Column(db.Float, nullable=True)
    buyer_lat = db.Column(db.Float, nullable=True)
    buyer_lng = db.Column(db.Float, nullable=True)
    product_lat = db.Column(db.Float, nullable=True)
    product_lng = db.Column(db.Float, nullable=True)

    # Product Snapshots
    product_name_snapshot = db.Column(db.String(100), nullable=True)
    product_price_snapshot = db.Column(db.Float, nullable=True)
    product_category_snapshot = db.Column(db.String(50), nullable=True)
    product_quantity_snapshot = db.Column(db.String(50), nullable=True)
    product_unit_snapshot = db.Column(db.String(20), nullable=True)

    # Farmer & Farm Snapshots
    farmer_name_snapshot = db.Column(db.String(100), nullable=True)
    farm_name_snapshot = db.Column(db.String(100), nullable=True)

    # Pickup Snapshot
    pickup_instructions_snapshot = db.Column(db.Text, nullable=True)

    # Delivery Snapshots
    delivery_type_snapshot = db.Column(db.String(20), nullable=True)
    delivery_available_snapshot = db.Column(db.Boolean, nullable=True)
    delivery_price_per_km_snapshot = db.Column(db.Float, nullable=True)

    # Trust & Verification Snapshots
    farmer_trust_score_snapshot = db.Column(db.Float, nullable=True)
    farm_verification_status_snapshot = db.Column(db.String(20), nullable=True)
    community_verification_status_snapshot = db.Column(db.String(20), nullable=True)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.String(20), default="info")  # 'info', 'success', 'warning', 'error', 'order'
    message = db.Column(db.String(255), nullable=False)
    order_id = db.Column(db.Integer, nullable=True)
    read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref="notifications")

class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=True)
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    feedback = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class MarketPrice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), nullable=False)
    market_name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    location = db.Column(db.String(100), nullable=False)
    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=True)

    user = db.relationship("User", backref="audit_events")


class LocationAudit(db.Model):
    __tablename__ = 'location_audit'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    old_lat = db.Column(db.Float, nullable=True)
    old_lng = db.Column(db.Float, nullable=True)
    new_lat = db.Column(db.Float, nullable=True)
    new_lng = db.Column(db.Float, nullable=True)
    change_distance_km = db.Column(db.Float, nullable=True)
    change_method = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("location_audits", lazy=True))


class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), nullable=False)
    otp_hash = db.Column(db.String(100), nullable=False)
    otp_expires_at = db.Column(db.DateTime, nullable=False)
    verification_attempts = db.Column(db.Integer, default=0, nullable=False)
    reset_token = db.Column(db.String(255), nullable=True)
    token_expires_at = db.Column(db.DateTime, nullable=True)
    is_used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class SMSAuditLog(db.Model):
    __tablename__ = 'sms_audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    phone = db.Column(db.String(20), nullable=False)
    event_type = db.Column(db.String(50), nullable=False)  # 'OTP_RECOVERY', 'OTP_SIGNUP', 'NEW_ORDER_ALERT'
    provider = db.Column(db.String(20), nullable=False)    # 'MSG91', 'MOCK'
    template_id = db.Column(db.String(50), nullable=True)
    message_content = db.Column(db.Text, nullable=False)
    provider_reference = db.Column(db.String(100), nullable=True)
    delivery_status = db.Column(db.String(20), default="PENDING")  # PENDING, SENT, FAILED
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("sms_logs", lazy=True))


class RegistrationOTP(db.Model):
    __tablename__ = 'registration_otps'
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), nullable=False)
    otp_hash = db.Column(db.String(100), nullable=False)
    otp_expires_at = db.Column(db.DateTime, nullable=False)
    verification_attempts = db.Column(db.Integer, default=0, nullable=False)
    registration_token = db.Column(db.String(255), nullable=True)
    token_expires_at = db.Column(db.DateTime, nullable=True)
    is_used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

