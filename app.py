from flask import Flask, jsonify
from flask_cors import CORS
from models import db
import os

# Load .env if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

def create_app():
    app = Flask(__name__)


    CORS(app)

    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return response


    basedir = os.path.abspath(os.path.dirname(__file__))
    if os.environ.get('TESTING') == 'true':
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'test_naadan.db') + '?timeout=30'
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'naadan.db') + '?timeout=30'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    
    with app.app_context():
        # Run SQLite migrations for columns if they do not exist
        migrations = [
            'ALTER TABLE "user" ADD COLUMN delivery_available BOOLEAN DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN delivery_price_per_km FLOAT DEFAULT 10.0',
            'ALTER TABLE "order" ADD COLUMN shipping_phone VARCHAR(20)',
            'ALTER TABLE "order" ADD COLUMN shipping_address TEXT',
            'ALTER TABLE "user" ADD COLUMN is_verified BOOLEAN DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN verification_status VARCHAR(20) DEFAULT "NONE"',
            'ALTER TABLE "user" ADD COLUMN aadhaar_number VARCHAR(20)',
            'ALTER TABLE "user" ADD COLUMN panchayat_id VARCHAR(50)',
            'ALTER TABLE "user" ADD COLUMN phone_verified BOOLEAN DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN farm_verified BOOLEAN DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN farm_photos_json TEXT',
            'ALTER TABLE "user" ADD COLUMN farm_verification_status VARCHAR(20) DEFAULT "NONE"',
            'ALTER TABLE "user" ADD COLUMN community_verified BOOLEAN DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN community_doc_type VARCHAR(50)',
            'ALTER TABLE "user" ADD COLUMN community_doc_status VARCHAR(20) DEFAULT "NONE"',
            'ALTER TABLE "user" ADD COLUMN trust_score FLOAT DEFAULT 0.0',
            'ALTER TABLE "user" ADD COLUMN average_rating FLOAT DEFAULT 0.0',
            'ALTER TABLE "user" ADD COLUMN total_ratings INTEGER DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN completed_orders_count INTEGER DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN response_speed VARCHAR(20) DEFAULT "Normal"',
            'ALTER TABLE "rating" ADD COLUMN order_id INTEGER',
            'ALTER TABLE "product" ADD COLUMN delivery_type VARCHAR(20) DEFAULT "both"',
            'ALTER TABLE "product" ADD COLUMN reserved_quantity FLOAT DEFAULT 0.0',
            'ALTER TABLE "user" ADD COLUMN is_farmer BOOLEAN DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN is_buyer BOOLEAN DEFAULT 0',
            'ALTER TABLE "user" ADD COLUMN is_admin BOOLEAN DEFAULT 0',
            'ALTER TABLE "product" ADD COLUMN idempotency_key VARCHAR(100)',
            'ALTER TABLE "order" ADD COLUMN idempotency_key VARCHAR(100)',
            'ALTER TABLE "order" ADD COLUMN delivered_at DATETIME',
            'ALTER TABLE "order" ADD COLUMN completed_at DATETIME',
            'ALTER TABLE "order" ADD COLUMN completed_by VARCHAR(20)',
            'ALTER TABLE "order" ADD COLUMN completion_reason VARCHAR(255)',
            "UPDATE \"order\" SET status = 'Pending Payment' WHERE status = 'PendingPayment'",
            "UPDATE \"order\" SET status = 'Waiting Farmer Confirmation' WHERE status = 'WaitingFarmerConfirmation'",
            "UPDATE \"order\" SET status = 'Accepted' WHERE status = 'Confirmed' OR status = 'Accepted' OR status = 'ACCEPTED'",
            "UPDATE \"order\" SET status = 'Out For Delivery' WHERE status = 'Shipped'",
            "UPDATE \"order\" SET status = 'Waiting Customer Confirmation' WHERE status = 'Delivered'"
        ]
        
        for mig in migrations:
            try:
                db.session.execute(db.text(mig))
                db.session.commit()
            except Exception:
                db.session.rollback()
        db.create_all()
        
        # Initialize roles for existing users
        try:
            # If they don't have roles enabled yet, initialize them
            # For farmers: is_farmer = 1, is_buyer = 0
            db.session.execute(db.text('UPDATE "user" SET is_farmer = 1 WHERE role = "farmer" AND is_farmer = 0 AND is_buyer = 0'))
            # For buyers: is_buyer = 1, is_farmer = 0
            db.session.execute(db.text('UPDATE "user" SET is_buyer = 1 WHERE role = "buyer" AND is_buyer = 0 AND is_farmer = 0'))
            # For admins: is_admin = 1
            db.session.execute(db.text('UPDATE "user" SET is_admin = 1 WHERE role = "admin" AND is_admin = 0'))
            db.session.commit()
            print("[MIGRATION] User role flags initialized successfully!")
        except Exception as role_mig_err:
            print("[MIGRATION] User role flags initialization failed:", str(role_mig_err))
            db.session.rollback()

    @app.route('/api/health', methods=['GET'])
    def health_check():
        return jsonify({"status": "healthy"}), 200

    # Register Blueprints
    from routes_auth import auth_bp
    from routes_products import products_bp
    from routes_orders import orders_bp
    from routes_verification import verification_bp
    from routes_market import market_bp
    from routes_payment import payment_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(products_bp, url_prefix='/api/products')
    app.register_blueprint(orders_bp, url_prefix='/api/orders')
    app.register_blueprint(verification_bp, url_prefix='/api/verify')
    app.register_blueprint(market_bp, url_prefix='/api/market')
    app.register_blueprint(payment_bp, url_prefix='/api/payment')

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
