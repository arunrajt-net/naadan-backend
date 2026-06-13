import os
import json
import base64
import unittest
from io import BytesIO

# Set TESTING environment variable before importing create_app
os.environ['TESTING'] = 'true'

from app import create_app, db
from models import User, Product, Order, AuditEvent

def make_dummy_token(user_id, email="test@naadan.com", name="Test User"):
    payload = {
        "user_id": user_id,
        "email": email,
        "name": name,
        "phone_number": "9876543210"
    }
    payload_json = json.dumps(payload)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode('utf-8')).decode('utf-8').rstrip('=')
    return f"header.{payload_b64}.signature"

class DirectUPIPaymentWorkflowTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        
        # Recreate all tables
        db.drop_all()
        db.create_all()
        
        # Setup UIDs
        self.farmer_uid = "farmer-123"
        self.buyer_uid = "buyer-456"
        self.admin_uid = "admin-uid"  # matches the hardcoded admin-session-token check
        
        # Create test users
        self.farmer = User(
            firebase_uid=self.farmer_uid,
            email="farmer@test.com",
            name="Farmer John",
            role="farmer",
            phone="9876543211",
            is_farmer=True,
            is_buyer=False,
            is_admin=False,
            is_verified=True,
            verification_status="VERIFIED"
        )
        
        self.buyer = User(
            firebase_uid=self.buyer_uid,
            email="buyer@test.com",
            name="Buyer Jane",
            role="buyer",
            phone="9876543212",
            is_farmer=False,
            is_buyer=True,
            is_admin=False,
            is_verified=True,
            verification_status="VERIFIED"
        )
        
        db.session.add(self.farmer)
        db.session.add(self.buyer)
        db.session.commit()
        
        # Create tokens
        self.farmer_token = make_dummy_token(self.farmer_uid, self.farmer.email, self.farmer.name)
        self.buyer_token = make_dummy_token(self.buyer_uid, self.buyer.email, self.buyer.name)
        self.admin_token = "admin-session-token" # matches the app.py admin bypass

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_payment_workflow(self):
        # 1. Try to list product without completing farmer setup (no UPI/location/landmark)
        headers = {
            "Authorization": f"Bearer {self.farmer_token}",
            "X-Active-Role": "farmer"
        }
        product_data = {
            "name": "Organic Mangoes",
            "price": 80.0,
            "quantity": "50 kg",
            "category": "Produce"
        }
        
        res = self.client.post("/api/products/", json=product_data, headers=headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Farmer setup incomplete", res.json["msg"])
        self.assertIn("UPI ID Missing or Invalid", res.json["errors"])
        self.assertIn("GPS Location Not Configured", res.json["errors"])
        self.assertIn("Pickup Landmark Missing", res.json["errors"])
        
        # Configure valid UPI format
        self.farmer.upi_id = "john@okaxis"
        db.session.commit()
        
        # Now it should fail because Farm Location is None
        res = self.client.post("/api/products/", json=product_data, headers=headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("GPS Location Not Configured", res.json["errors"])
        self.assertIn("Pickup Landmark Missing", res.json["errors"])
        self.assertNotIn("UPI ID Missing or Invalid", res.json["errors"])
        
        # Configure Farm Location
        self.farmer.lat = 10.0234
        self.farmer.lng = 76.0567
        db.session.commit()
        
        # Now it should fail because Pickup Landmark is empty
        res = self.client.post("/api/products/", json=product_data, headers=headers)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Pickup Landmark Missing", res.json["errors"])
        self.assertNotIn("GPS Location Not Configured", res.json["errors"])
        
        # Configure Pickup Landmark
        self.farmer.pickup_landmark = "Grama Panchayat Office Ranni"
        db.session.commit()
        
        # 2. List the product successfully now that setup is complete
        res = self.client.post("/api/products/", json=product_data, headers=headers)
        self.assertEqual(res.status_code, 201)
        product_id = res.json["id"]
        
        # Verify product is listed and exists
        product = Product.query.get(product_id)
        self.assertEqual(product.name, "Organic Mangoes")
        self.assertEqual(product.price, 80.0)
        self.assertEqual(product.total_stock, 50.0) # check parser works
        
        # 3. Place an order from the buyer
        buyer_headers = {
            "Authorization": f"Bearer {self.buyer_token}",
            "X-Active-Role": "buyer"
        }
        order_data = {
            "product_id": product_id,
            "quantity_ordered": 5,
            "delivery_type": "Pickup",
            "payment_method": "UPI",
            "shipping_phone": "9876543212"
        }
        res = self.client.post("/api/orders/", json=order_data, headers=buyer_headers)
        self.assertEqual(res.status_code, 201)
        order_id = res.json["id"]
        
        # Verify order state is Pending Payment and RESERVED quantity is correct
        order = Order.query.get(order_id)
        self.assertEqual(order.status, "Pending Payment")
        self.assertEqual(order.payment_status, "PENDING_PAYMENT")
        
        product = Product.query.get(product_id)
        self.assertEqual(product.reserved_quantity, 5.0)
        self.assertEqual(product.total_stock, 50.0) # stock is not deducted yet
        
        # 4. Upload payment screenshot proof as buyer
        screenshot_data = {
            "screenshot": (BytesIO(b"fake_image_bytes"), "payment.png"),
            "utr_number": "123456789012"
        }
        res = self.client.post(
            f"/api/orders/{order_id}/submit-proof",
            data=screenshot_data,
            content_type="multipart/form-data",
            headers=buyer_headers
        )
        self.assertEqual(res.status_code, 200)
        
        order = Order.query.get(order_id)
        self.assertEqual(order.status, "Waiting Farmer Confirmation")
        self.assertEqual(order.payment_status, "PAYMENT_SUBMITTED")
        self.assertEqual(order.utr_number, "123456789012")
        self.assertTrue(order.payment_screenshot_url.startswith("payment_proofs/"))
        
        # Clean up screenshot files from filesystem after creation
        proof_path = os.path.join(self.app.root_path, "uploads", order.payment_screenshot_url)
        
        # 5. Reject payment as farmer
        verify_data = {
            "action": "REJECT",
            "rejection_reason": "No payment received"
        }
        res = self.client.post(f"/api/orders/{order_id}/verify-payment", json=verify_data, headers=headers)
        self.assertEqual(res.status_code, 200)
        
        order = Order.query.get(order_id)
        self.assertEqual(order.status, "Pending Payment")
        self.assertEqual(order.payment_status, "PAYMENT_REJECTED")
        self.assertEqual(order.payment_rejection_reason, "No payment received")
        
        # 6. Re-submit corrected proof
        corrected_data = {
            "screenshot": (BytesIO(b"fake_image_bytes_corrected"), "corrected.jpg"),
            "utr_number": "999999999999"
        }
        res = self.client.post(
            f"/api/orders/{order_id}/submit-proof",
            data=corrected_data,
            content_type="multipart/form-data",
            headers=buyer_headers
        )
        self.assertEqual(res.status_code, 200)
        
        order = Order.query.get(order_id)
        self.assertEqual(order.status, "Waiting Farmer Confirmation")
        self.assertEqual(order.payment_status, "PAYMENT_SUBMITTED")
        self.assertEqual(order.utr_number, "999999999999")
        self.assertIsNone(order.payment_rejection_reason) # cleared
        
        # Clean up second screenshot
        proof_path_2 = os.path.join(self.app.root_path, "uploads", order.payment_screenshot_url)
        
        # 7. Approve payment as farmer
        approve_data = {
            "action": "APPROVE"
        }
        res = self.client.post(f"/api/orders/{order_id}/verify-payment", json=approve_data, headers=headers)
        self.assertEqual(res.status_code, 200)
        
        order = Order.query.get(order_id)
        self.assertEqual(order.status, "Accepted")
        self.assertEqual(order.payment_status, "PAYMENT_CONFIRMED")
        self.assertIsNotNone(order.payment_verified_at)
        self.assertEqual(order.payment_verified_by, "Farmer John")
        
        # Verify stock is still reserved (not deducted yet)
        product = Product.query.get(product_id)
        self.assertEqual(product.total_stock, 50.0)
        self.assertEqual(product.reserved_quantity, 5.0)

        # 7.5 Advance order to Completed to trigger official stock deduction
        # Farmer packs order
        res = self.client.put(f"/api/orders/{order_id}/status", json={"status": "Packed"}, headers=headers)
        self.assertEqual(res.status_code, 200)

        # Farmer marks out for delivery
        res = self.client.put(f"/api/orders/{order_id}/status", json={"status": "Out For Delivery"}, headers=headers)
        self.assertEqual(res.status_code, 200)

        # Buyer confirms delivery (Completed)
        res = self.client.put(f"/api/orders/{order_id}/status", json={"status": "Completed"}, headers=buyer_headers)
        self.assertEqual(res.status_code, 200)

        # Verify stock has been officially DEDUCTED and RESERVED is cleared
        product = Product.query.get(product_id)
        self.assertEqual(product.total_stock, 45.0)
        self.assertEqual(product.reserved_quantity, 0.0)
        
        # 8. Check admin read-only payment log endpoint
        admin_headers = {
            "Authorization": "Bearer admin-session-token",
            "X-Active-Role": "admin"
        }
        res = self.client.get("/api/admin/payments", headers=admin_headers)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(len(res.json) >= 1)
        audit_row = next(r for r in res.json if r["id"] == order_id)
        self.assertEqual(audit_row["payment_status"], "PAYMENT_CONFIRMED")
        self.assertEqual(audit_row["utr_number"], "999999999999")
        self.assertEqual(audit_row["payment_verified_by"], "Farmer John")
        
        # Cleanup proof files on teardown
        for path in [proof_path, proof_path_2]:
            if os.path.exists(path):
                os.remove(path)

if __name__ == '__main__':
    unittest.main()
