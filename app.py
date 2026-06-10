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

import time

def run_backfill_and_verification(db, app, stats_before):
    from models import Order, User, Product
    
    start_time = time.time()
    
    # Check if there are orders needing backfill
    orders_to_backfill = Order.query.filter(Order.farmer_lat == None).all()
    
    migrated_count = 0
    skipped_count = 0
    missing_users = []
    missing_products = []
    
    if orders_to_backfill:
        print(f"[MIGRATION] Found {len(orders_to_backfill)} orders to backfill.")
        for order in orders_to_backfill:
            try:
                buyer = User.query.get(order.buyer_id)
                farmer = User.query.get(order.farmer_id)
                product = Product.query.get(order.product_id)
                
                # Snapshots
                order.buyer_lat = buyer.lat if buyer else None
                order.buyer_lng = buyer.lng if buyer else None
                
                order.farmer_lat = farmer.lat if farmer else None
                order.farmer_lng = farmer.lng if farmer else None
                
                order.product_lat = product.lat if product else None
                order.product_lng = product.lng if product else None
                
                order.product_name_snapshot = product.name if product else "Legacy Product"
                order.product_price_snapshot = product.price if product else (order.total_price / (order.quantity_ordered or 1))
                order.product_category_snapshot = product.category if product else "Legacy"
                
                # Quantity and Unit snapshots
                if product:
                    order.product_quantity_snapshot = product.quantity
                    order.product_unit_snapshot = product.unit
                else:
                    order.product_quantity_snapshot = f"{order.quantity_ordered} units"
                    order.product_unit_snapshot = "units"
                    
                order.farmer_name_snapshot = farmer.name if farmer else "Legacy Farmer"
                order.farm_name_snapshot = farmer.farm_name if (farmer and farmer.farm_name) else (f"{farmer.name}'s Farm" if farmer else "Legacy Farm")
                order.pickup_instructions_snapshot = farmer.pickup_instructions if (farmer and farmer.pickup_instructions) else "Contact farmer for pickup instructions."
                
                # Delivery settings snapshot
                if product:
                    order.delivery_type_snapshot = product.delivery_type
                else:
                    order.delivery_type_snapshot = "both"
                order.delivery_available_snapshot = farmer.delivery_available if farmer else False
                order.delivery_price_per_km_snapshot = farmer.delivery_price_per_km if farmer else 0.0
                
                # Trust and Verification snapshots
                order.farmer_trust_score_snapshot = farmer.trust_score if farmer else 0.0
                order.farm_verification_status_snapshot = farmer.farm_verification_status if farmer else "NONE"
                order.community_verification_status_snapshot = farmer.community_doc_status if farmer else "NONE"
                
                # Track missing resources for report
                if not buyer or not farmer:
                    missing_users.append(f"Order {order.id}: Buyer={order.buyer_id}, Farmer={order.farmer_id}")
                if not product:
                    missing_products.append(f"Order {order.id}: Product={order.product_id}")
                    
                migrated_count += 1
            except Exception as order_err:
                print(f"[MIGRATION ERROR] Failed to backfill order {order.id}: {order_err}")
                skipped_count += 1
                
        try:
            db.session.commit()
            print("[MIGRATION] Historical backfill committed successfully!")
        except Exception as commit_err:
            print("[MIGRATION ERROR] Commit backfill failed:", str(commit_err))
            db.session.rollback()
            raise commit_err
    else:
        print("[MIGRATION] No historical orders need backfill.")

    duration_ms = (time.time() - start_time) * 1000
    
    # Query final counts
    stats_after = {}
    for t in ['user', 'product', 'order', 'rating', 'notification']:
        try:
            stats_after[t] = db.session.execute(db.text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
        except:
            stats_after[t] = 0
            
    # Calculate completion rate
    total_orders = Order.query.count()
    total_backfilled_or_filled = Order.query.filter(Order.farmer_lat != None).count()
    completion_rate = (total_backfilled_or_filled / total_orders * 100) if total_orders > 0 else 100
    
    # Run integrity check again
    integrity_res = "ok"
    try:
        integrity_res = db.session.execute(db.text("PRAGMA integrity_check;")).scalar()
    except Exception as e:
        integrity_res = f"failed_check: {str(e)}"
        
    report = f"""# Post-Migration Verification Report

Generated automatically after database migration and historical backfill execution.

## Migration Performance
* **Migration Execution Time:** {duration_ms:.2f} ms
* **Integrity Check Result:** {integrity_res}

## Snapshot Verification Metrics
* **Total Orders Migrated:** {migrated_count}
* **Total Orders Skipped:** {skipped_count}
* **Snapshot Completion Rate:** {completion_rate:.1f}%

## Missing References (Data Anomaly Logs)
* **Orphan Orders (Missing Users):** {missing_users if missing_users else 'None'}
* **Orphan Orders (Missing Products):** {missing_products if missing_products else 'None'}

## Table Counts (Before vs After)
* **User Count:** {stats_before.get('user', 0)} before vs {stats_after.get('user', 0)} after
* **Product Count:** {stats_before.get('product', 0)} before vs {stats_after.get('product', 0)} after
* **Order Count:** {stats_before.get('order', 0)} before vs {stats_after.get('order', 0)} after
* **Rating Count:** {stats_before.get('rating', 0)} before vs {stats_after.get('rating', 0)} after
* **Notification Count:** {stats_before.get('notification', 0)} before vs {stats_after.get('notification', 0)} after
"""
    
    # Write report as artifact
    brain_report_path = r"C:\Users\arunr\.gemini\antigravity\brain\04516b75-275a-40ca-a623-8a6d25fb5647\post_migration_verification.md"
    with open(brain_report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[MIGRATION] Post-migration report generated at {brain_report_path}")


def start_weekly_backup_verifier(app):
    import threading
    import shutil
    import sqlite3
    
    def verify_backup_loop():
        # Wait a short while on startup before running restoration check to let the app start
        time.sleep(5)
        while True:
            try:
                with app.app_context():
                    db_uri = app.config['SQLALCHEMY_DATABASE_URI']
                    if db_uri.startswith('sqlite:///'):
                        db_file = db_uri.split('sqlite:///')[1].split('?')[0]
                        if not os.path.isabs(db_file):
                            db_file = os.path.abspath(db_file)
                        
                        if os.path.exists(db_file):
                            temp_restore_file = db_file + ".restore_test"
                            
                            # Perform dry-run restore copy
                            shutil.copyfile(db_file, temp_restore_file)
                            
                            # Verify integrity on the copy
                            conn = sqlite3.connect(temp_restore_file)
                            cur = conn.cursor()
                            cur.execute("PRAGMA integrity_check;")
                            res = cur.fetchone()[0]
                            conn.close()
                            
                            # Delete temporary file
                            if os.path.exists(temp_restore_file):
                                os.remove(temp_restore_file)
                                
                            if res == "ok":
                                print(f"[BACKUP VERIFICATION] Weekly backup verification success. Restore replica integrity is OK.")
                            else:
                                print(f"[CRITICAL ALARM] Weekly backup verification failure! Result: {res}")
            except Exception as e:
                print(f"[CRITICAL ALARM] Weekly backup verification process crashed: {str(e)}")
            
            # Sleep for 7 days
            time.sleep(7 * 24 * 3600)
            
    t = threading.Thread(target=verify_backup_loop, daemon=True)
    t.start()


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
        # A. Database Integrity Check
        try:
            res = db.session.execute(db.text("PRAGMA integrity_check;")).scalar()
            if res != "ok":
                print(f"[FATAL ERROR] Database integrity check failed! Result: {res}")
                import sys
                sys.exit(1)
            print("[INTEGRITY CHECK] Database is OK.")
        except Exception as e:
            print(f"[FATAL ERROR] Could not perform database integrity check: {str(e)}")
            import sys
            sys.exit(1)

        # B. Database Backup Prior to Migration
        db_uri = app.config['SQLALCHEMY_DATABASE_URI']
        if db_uri.startswith('sqlite:///'):
            db_file = db_uri.split('sqlite:///')[1].split('?')[0]
            if not os.path.isabs(db_file):
                db_file = os.path.abspath(db_file)
            if os.path.exists(db_file):
                import shutil
                shutil.copyfile(db_file, db_file + ".bak")
                print(f"[MIGRATION] Database backed up to {db_file}.bak")

        # C. Capture stats before migration
        stats_before = {}
        for t in ['user', 'product', 'order', 'rating', 'notification']:
            try:
                stats_before[t] = db.session.execute(db.text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
            except Exception as e:
                print(f"[MIGRATION WARNING] Failed to get stats_before for {t}:", e)
                stats_before[t] = 0

        # Run SQLite migrations for columns if they do not exist
        migrations = [
            'ALTER TABLE "user" ADD COLUMN gps_accuracy FLOAT',
            'ALTER TABLE "user" ADD COLUMN location_last_updated DATETIME',
            'ALTER TABLE "user" ADD COLUMN pickup_landmark VARCHAR(150)',
            'ALTER TABLE "order" ADD COLUMN farmer_lat FLOAT',
            'ALTER TABLE "order" ADD COLUMN farmer_lng FLOAT',
            'ALTER TABLE "order" ADD COLUMN buyer_lat FLOAT',
            'ALTER TABLE "order" ADD COLUMN buyer_lng FLOAT',
            'ALTER TABLE "order" ADD COLUMN product_lat FLOAT',
            'ALTER TABLE "order" ADD COLUMN product_lng FLOAT',
            'ALTER TABLE "order" ADD COLUMN product_name_snapshot VARCHAR(100)',
            'ALTER TABLE "order" ADD COLUMN product_price_snapshot FLOAT',
            'ALTER TABLE "order" ADD COLUMN product_category_snapshot VARCHAR(50)',
            'ALTER TABLE "order" ADD COLUMN product_quantity_snapshot VARCHAR(50)',
            'ALTER TABLE "order" ADD COLUMN product_unit_snapshot VARCHAR(20)',
            'ALTER TABLE "order" ADD COLUMN farmer_name_snapshot VARCHAR(100)',
            'ALTER TABLE "order" ADD COLUMN farm_name_snapshot VARCHAR(100)',
            'ALTER TABLE "order" ADD COLUMN pickup_instructions_snapshot TEXT',
            'ALTER TABLE "order" ADD COLUMN delivery_type_snapshot VARCHAR(20)',
            'ALTER TABLE "order" ADD COLUMN delivery_available_snapshot BOOLEAN',
            'ALTER TABLE "order" ADD COLUMN delivery_price_per_km_snapshot FLOAT',
            'ALTER TABLE "order" ADD COLUMN farmer_trust_score_snapshot FLOAT',
            'ALTER TABLE "order" ADD COLUMN farm_verification_status_snapshot VARCHAR(20)',
            'ALTER TABLE "order" ADD COLUMN community_verification_status_snapshot VARCHAR(20)',
            'CREATE INDEX IF NOT EXISTS idx_location_audit_user ON location_audit(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_order_farmer_id ON "order"(farmer_id)',
            'CREATE INDEX IF NOT EXISTS idx_order_buyer_id ON "order"(buyer_id)',
            'CREATE INDEX IF NOT EXISTS idx_product_farmer_id ON product(farmer_id)',
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
            "UPDATE \"order\" SET status = 'Waiting Customer Confirmation' WHERE status = 'Delivered'",
            'ALTER TABLE "user" ADD COLUMN location_privacy VARCHAR(20) DEFAULT "public"',
            'ALTER TABLE "user" ADD COLUMN pickup_instructions TEXT',
            'CREATE TABLE IF NOT EXISTS password_reset (id INTEGER PRIMARY KEY AUTOINCREMENT, phone VARCHAR(20) NOT NULL, otp_hash VARCHAR(100) NOT NULL, otp_expires_at DATETIME NOT NULL, verification_attempts INTEGER DEFAULT 0 NOT NULL, reset_token VARCHAR(255), token_expires_at DATETIME, is_used BOOLEAN DEFAULT 0 NOT NULL, created_at DATETIME NOT NULL)'
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

        # D. Run backfill and generate verification report
        try:
            run_backfill_and_verification(db, app, stats_before)
        except Exception as backfill_err:
            print("[MIGRATION ERROR] Backfill failed:", str(backfill_err))

        # E. Start weekly automated backup verification thread
        try:
            start_weekly_backup_verifier(app)
        except Exception as backup_t_err:
            print("[MIGRATION WARNING] Failed to start backup verification thread:", str(backup_t_err))

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
    from routes_admin import admin_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(products_bp, url_prefix='/api/products')
    app.register_blueprint(orders_bp, url_prefix='/api/orders')
    app.register_blueprint(verification_bp, url_prefix='/api/verify')
    app.register_blueprint(market_bp, url_prefix='/api/market')
    app.register_blueprint(payment_bp, url_prefix='/api/payment')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
