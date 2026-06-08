import os
import unittest
import json
from datetime import datetime

# Set testing environment variable
os.environ['TESTING'] = 'true'

from app import create_app
from models import db, User, Product, Order, LocationAudit

class TestSnapshotIntegrity(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        # Clean up databases and ensure structure is correct
        db.create_all()
        
        # Create test users
        self.buyer = User(
            firebase_uid="test-buyer-uid-999",
            name="Test Buyer",
            email="testbuyer@naadan.com",
            role="buyer",
            is_buyer=True,
            lat=11.11,
            lng=75.55
        )
        self.farmer = User(
            firebase_uid="test-farmer-uid-999",
            name="Test Farmer",
            email="testfarmer@naadan.com",
            role="farmer",
            is_farmer=True,
            lat=11.22,
            lng=75.66,
            farm_name="Original Farm",
            pickup_instructions="Original instructions",
            delivery_available=True,
            delivery_price_per_km=15.0,
            trust_score=85.0,
            farm_verification_status="VERIFIED",
            community_doc_status="VERIFIED"
        )
        
        db.session.add(self.buyer)
        db.session.add(self.farmer)
        db.session.commit()
        
        # Create test product
        self.product = Product(
            farmer_id=self.farmer.id,
            name="Original Mangoes",
            category="Fruits",
            price=150.0,
            quantity="5 kg",
            lat=11.22,
            lng=75.66,
            is_available=True,
            delivery_type="both"
        )
        db.session.add(self.product)
        db.session.commit()

    def tearDown(self):
        # Clean up records
        Order.query.filter_by(buyer_id=self.buyer.id).delete()
        Product.query.filter_by(farmer_id=self.farmer.id).delete()
        LocationAudit.query.filter_by(user_id=self.farmer.id).delete()
        User.query.filter(User.id.in_([self.buyer.id, self.farmer.id])).delete()
        db.session.commit()
        self.app_context.pop()

    def test_order_creation_freezes_snapshots(self):
        print("\n[TEST] Placing order...")
        
        # Simulate checkout / order placement
        new_order = Order(
            buyer_id=self.buyer.id,
            farmer_id=self.farmer.id,
            product_id=self.product.id,
            quantity_ordered=2,
            total_price=300.0,
            delivery_type="Pickup",
            delivery_vehicle="motorcycle",
            status="Pending Payment",
            
            # Snapshots
            buyer_lat=self.buyer.lat,
            buyer_lng=self.buyer.lng,
            farmer_lat=self.farmer.lat,
            farmer_lng=self.farmer.lng,
            product_lat=self.product.lat,
            product_lng=self.product.lng,
            
            product_name_snapshot=self.product.name,
            product_price_snapshot=self.product.price,
            product_category_snapshot=self.product.category,
            product_quantity_snapshot=self.product.quantity,
            product_unit_snapshot=self.product.unit,
            
            farmer_name_snapshot=self.farmer.name,
            farm_name_snapshot=self.farmer.farm_name,
            pickup_instructions_snapshot=self.farmer.pickup_instructions,
            
            delivery_type_snapshot=self.product.delivery_type,
            delivery_available_snapshot=self.farmer.delivery_available,
            delivery_price_per_km_snapshot=self.farmer.delivery_price_per_km,
            
            farmer_trust_score_snapshot=self.farmer.trust_score,
            farm_verification_status_snapshot=self.farmer.farm_verification_status,
            community_verification_status_snapshot=self.farmer.community_doc_status
        )
        db.session.add(new_order)
        db.session.commit()
        order_id = new_order.id
        print(f"Order created with ID: {order_id}")
        
        # 1. Modify Product details
        print("[TEST] Modifying original product details...")
        self.product.name = "Modified Mangoes (Cheap)"
        self.product.price = 50.0
        self.product.category = "Vegetables"
        self.product.lat = 12.99
        self.product.lng = 77.99
        db.session.commit()
        
        # 2. Modify Farmer details & GPS (following model validation rules)
        print("[TEST] Modifying original farmer details & coordinates...")
        self.farmer.name = "Modified Farmer"
        self.farmer.farm_name = "Modified Farm"
        self.farmer.pickup_instructions = "Modified instructions"
        self.farmer.lat = 13.11
        self.farmer.lng = 78.22
        # Set both values to be valid under model validation constraints
        self.farmer.delivery_available = False
        self.farmer.delivery_price_per_km = 0.0
        self.farmer.trust_score = 10.0
        self.farmer.farm_verification_status = "REVOKED"
        self.farmer.community_doc_status = "REVOKED"
        db.session.commit()
        
        # Query order details via API using test client
        print("[TEST] Querying order record from database to verify snapshots are frozen...")
        order = Order.query.get(order_id)
        
        self.assertEqual(order.product_name_snapshot, "Original Mangoes")
        self.assertEqual(order.product_price_snapshot, 150.0)
        self.assertEqual(order.product_category_snapshot, "Fruits")
        self.assertEqual(order.product_quantity_snapshot, "5 kg")
        
        self.assertEqual(order.farmer_lat, 11.22)
        self.assertEqual(order.farmer_lng, 75.66)
        self.assertEqual(order.buyer_lat, 11.11)
        self.assertEqual(order.buyer_lng, 75.55)
        
        self.assertEqual(order.farmer_name_snapshot, "Test Farmer")
        self.assertEqual(order.farm_name_snapshot, "Original Farm")
        self.assertEqual(order.pickup_instructions_snapshot, "Original instructions")
        
        self.assertEqual(order.delivery_available_snapshot, True)
        self.assertEqual(order.delivery_price_per_km_snapshot, 15.0)
        self.assertEqual(order.farmer_trust_score_snapshot, 85.0)
        self.assertEqual(order.farm_verification_status_snapshot, "VERIFIED")
        
        print("[SUCCESS] Database order snapshots successfully verified as frozen!")

if __name__ == '__main__':
    unittest.main()
