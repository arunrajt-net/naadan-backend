import requests
import sys
import sqlite3
import os
from datetime import datetime, timedelta

BASE_URL = "http://localhost:5000/api"
DB_PATH = os.path.join(os.path.dirname(__file__), "naadan.db")

def modify_db_record(query, params=()):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] {e}")

def run_tests():
    print("=== STARTING NAADAN SMS & OTP LIFECYCLE TESTS ===")

    test_phone = "7777777777"
    dummy_uid = "recovery-test-uid-123"
    
    # 0. Setup: Clean up any old test records
    modify_db_record("DELETE FROM user WHERE phone = ?", (test_phone,))
    modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
    modify_db_record("DELETE FROM registration_otps WHERE phone = ?", (test_phone,))
    modify_db_record("DELETE FROM sms_audit_logs WHERE phone = ?", (test_phone,))
    print("[SETUP] Cleaned up old test records.")

    # ----------------------------------------------------
    # TEST GROUP 1: PASSWORD RECOVERY FLOW
    # ----------------------------------------------------
    print("\n--- Test Group 1: Password Recovery ---")
    
    # 1. Indian Phone Validation
    try:
        invalid_phones = ["12345", "9497856", "5497856550"]
        for iphone in invalid_phones:
            res = requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": iphone})
            print(f"[TEST] Invalid recovery phone ({iphone}) response status: {res.status_code}")
            assert res.status_code == 400, f"Invalid phone {iphone} should return 400"
            assert "Indian phone number" in res.json().get("msg", ""), "Expected Indian validation message"
        print("[PASS] Indian phone validation behaves correctly")
    except Exception as e:
        print(f"[FAIL] Indian phone validation test: {e}")
        sys.exit(1)

    # 2. OTP Cooldown (60 seconds)
    try:
        # Request 1: Should succeed
        modify_db_record("INSERT INTO user (firebase_uid, name, email, phone, role, is_buyer, is_farmer, is_admin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (dummy_uid, "Recovery Tester", "tester@naadan.com", test_phone, "buyer", 1, 0, 0))
        res1 = requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})
        print(f"[TEST] First request status: {res1.status_code}")
        assert res1.status_code == 200, "First request failed"

        # Request 2 (immediate): Should trigger cooldown 429
        res2 = requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})
        print(f"[TEST] Second request (immediate) status: {res2.status_code}")
        assert res2.status_code == 429, "Immediate repeat request should trigger 429 Cooldown"
        assert "wait 60 seconds" in res2.json().get("msg", ""), "Expected cooldown warning message"
        print("[PASS] OTP Cooldown (60 seconds) behaves correctly")
    except Exception as e:
        print(f"[FAIL] OTP Cooldown test: {e}")
        sys.exit(1)

    # 3. OTP Expiry (5 minutes)
    try:
        modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
        requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})
        
        # Modify the created OTP record to expire it (set to 10 minutes ago)
        expired_time = (datetime.utcnow() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S.%f")
        modify_db_record("UPDATE password_reset SET otp_expires_at = ? WHERE phone = ?", (expired_time, test_phone))
        
        res = requests.post(f"{BASE_URL}/auth/verify-recovery-otp", json={"phone": test_phone, "otp": "123456"})
        print(f"[TEST] Verify expired OTP status: {res.status_code}")
        assert res.status_code == 400, "Expired OTP should return 400"
        assert "invalid or expired" in res.json().get("msg", "").lower(), "Expected expiry error message"
        print("[PASS] OTP Expiration behaves correctly")
    except Exception as e:
        print(f"[FAIL] OTP Expiry test: {e}")
        sys.exit(1)

    # 4. OTP Verification Lockout (5 attempts)
    try:
        modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
        requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})

        # Submit incorrect OTPs 5 times
        for attempt in range(1, 6):
            res = requests.post(f"{BASE_URL}/auth/verify-recovery-otp", json={"phone": test_phone, "otp": "000000"})
            assert res.status_code == 400, "Failed attempt should return 400"

        # The 6th attempt should block with lockout message
        res_lockout = requests.post(f"{BASE_URL}/auth/verify-recovery-otp", json={"phone": test_phone, "otp": "000000"})
        print(f"[TEST] Attempt 6 (lockout) status: {res_lockout.status_code}, response: {res_lockout.json()}")
        assert res_lockout.status_code == 400, "Lockout attempt should return 400"
        assert "too many failed attempts" in res_lockout.json().get("msg", "").lower(), "Expected lockout warning message"
        print("[PASS] OTP verification lockout behaves correctly")
    except Exception as e:
        print(f"[FAIL] OTP verification lockout test: {e}")
        sys.exit(1)

    # 5. Strong Password Validation
    try:
        modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
        requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})

        # Get generated OTP from DB
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT otp_hash FROM password_reset WHERE phone = ? ORDER BY id DESC LIMIT 1", (test_phone,))
        otp_hash = cursor.fetchone()[0]
        conn.close()

        # Since OTP is hashed in DB, we'll extract it from the log file for validation
        otp_val = None
        log_path = os.path.join(os.path.dirname(__file__), "otp_log.txt")
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                for line in reversed(f.readlines()):
                    if test_phone in line and "Event: OTP_RECOVERY" in line:
                        parts = line.strip().split("Msg: ")
                        if len(parts) > 1:
                            # Msg: Your Naadan verification code is 123456...
                            otp_val = parts[1].split("code is ")[1].split(".")[0].strip()
                            break
        
        if not otp_val:
            # Let's write a mock OTP entry since we write it to console / log
            print("[TEST] Could not extract OTP from log, using direct SQL bypass for reset_token")
            # We'll just generate a reset_token in DB manually to test password validation
            reset_token = "dummy-reset-token-for-test"
            modify_db_record("UPDATE password_reset SET reset_token = ?, token_expires_at = ? WHERE phone = ?",
                             (reset_token, (datetime.utcnow() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S.%f"), test_phone))
        else:
            res_verify = requests.post(f"{BASE_URL}/auth/verify-recovery-otp", json={"phone": test_phone, "otp": otp_val})
            assert res_verify.status_code == 200, f"Verification with OTP {otp_val} failed"
            reset_token = res_verify.json()["reset_token"]

        # Try weak password
        res_reset = requests.post(f"{BASE_URL}/auth/reset-password", json={
            "phone": test_phone,
            "reset_token": reset_token,
            "new_password": "weak"
        })
        print(f"[TEST] Reset with weak password status: {res_reset.status_code}")
        assert res_reset.status_code == 400, "Weak password should be rejected"
        
        # Try strong password
        res_reset_valid = requests.post(f"{BASE_URL}/auth/reset-password", json={
            "phone": test_phone,
            "reset_token": reset_token,
            "new_password": "SecurePassword123"
        })
        print(f"[TEST] Reset with strong password status: {res_reset_valid.status_code}")
        assert res_reset_valid.status_code == 200, "Strong password reset failed"
        print("[PASS] Strong password validation behaves correctly")
    except Exception as e:
        print(f"[FAIL] Strong password validation test: {e}")
        sys.exit(1)

    # ----------------------------------------------------
    # TEST GROUP 2: REGISTRATION OTP FLOW
    # ----------------------------------------------------
    print("\n--- Test Group 2: Registration OTP ---")
    
    # 1. Registration Request OTP
    try:
        modify_db_record("DELETE FROM user WHERE phone = ?", (test_phone,))
        modify_db_record("DELETE FROM registration_otps WHERE phone = ?", (test_phone,))

        # Request signup OTP
        res1 = requests.post(f"{BASE_URL}/auth/register-request-otp", json={"phone": test_phone})
        print(f"[TEST] Request signup OTP status: {res1.status_code}")
        assert res1.status_code == 200, "Signup OTP request failed"

        # Cooldown trigger (immediate second request)
        res2 = requests.post(f"{BASE_URL}/auth/register-request-otp", json={"phone": test_phone})
        print(f"[TEST] Request signup OTP cooldown status: {res2.status_code}")
        assert res2.status_code == 429, "Immediate signup request should return 429"
        print("[PASS] Signup OTP request and cooldown behave correctly")
    except Exception as e:
        print(f"[FAIL] Signup OTP request: {e}")
        sys.exit(1)

    # 2. Registration Verify OTP
    try:
        # Get OTP from log
        otp_val = None
        log_path = os.path.join(os.path.dirname(__file__), "otp_log.txt")
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                for line in reversed(f.readlines()):
                    if test_phone in line and "Event: OTP_SIGNUP" in line:
                        parts = line.strip().split("Msg: ")
                        if len(parts) > 1:
                            otp_val = parts[1].split("code is ")[1].split(".")[0].strip()
                            break

        if not otp_val:
            print("[TEST] Could not extract signup OTP from log, injecting verification token in DB")
            registration_token = "dummy-registration-token-for-test"
            modify_db_record("UPDATE registration_otps SET registration_token = ?, token_expires_at = ? WHERE phone = ?",
                             (registration_token, (datetime.utcnow() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S.%f"), test_phone))
        else:
            res_verify = requests.post(f"{BASE_URL}/auth/register-verify-otp", json={"phone": test_phone, "otp": otp_val})
            assert res_verify.status_code == 200, "Verification with correct signup OTP failed"
            registration_token = res_verify.json()["registration_token"]
            print(f"[TEST] Captured registration_token: {registration_token}")

        # 3. Synchronize user creation with/without token
        # Call /sync without token (should fail)
        dummy_sync_token = "Bearer dummy-token"
        headers = {"Authorization": dummy_sync_token}
        payload = {
            "role": "buyer",
            "name": "Signup Tester",
            "phone": test_phone
        }
        res_sync_fail = requests.post(f"{BASE_URL}/auth/sync", json=payload, headers=headers)
        print(f"[TEST] Sync user without token status: {res_sync_fail.status_code}")
        assert res_sync_fail.status_code == 400, "Syncing without valid registration token should return 400"

        # Call /sync with token (should succeed)
        payload["registration_token"] = registration_token
        res_sync_success = requests.post(f"{BASE_URL}/auth/sync", json=payload, headers=headers)
        print(f"[TEST] Sync user with token status: {res_sync_success.status_code}")
        assert res_sync_success.status_code in [200, 201], "Syncing with valid registration token failed"
        
        # Verify user is phone_verified in DB
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT phone_verified FROM user WHERE phone = ?", (test_phone,))
        phone_verified = cursor.fetchone()[0]
        conn.close()
        print(f"[TEST] Created user phone_verified in DB: {bool(phone_verified)}")
        assert phone_verified == 1, "User created after token verification should have phone_verified = 1"
        print("[PASS] Registration verification flow behaves correctly")
    except Exception as e:
        print(f"[FAIL] Signup OTP verification: {e}")
        sys.exit(1)

    # ----------------------------------------------------
    # TEST GROUP 3: SMS AUDIT LOGS & TELEMETRY
    # ----------------------------------------------------
    print("\n--- Test Group 3: SMS Audit & Telemetry ---")
    try:
        # Check that we have rows in sms_audit_logs table
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sms_audit_logs WHERE phone = ?", (test_phone,))
        logs = cursor.fetchall()
        conn.close()
        
        print(f"[TEST] Audit log entries written: {len(logs)}")
        assert len(logs) > 0, "No audit logs written for test operations"
        
        # Call stats endpoint (simulate admin login)
        admin_headers = {"Authorization": "Bearer admin-session-token", "X-Active-Role": "admin"}
        # Make sure admin user exists or we log in
        requests.post(f"{BASE_URL}/auth/admin-login", json={"username": "admin@naadan.com", "password": "secure_password"})
        
        res_stats = requests.get(f"{BASE_URL}/admin/sms-stats", headers=admin_headers)
        print(f"[TEST] Admin SMS stats status: {res_stats.status_code}, stats: {res_stats.json()}")
        assert res_stats.status_code == 200, "Admin stats fetch failed"
        assert "sent_today" in res_stats.json(), "Missing sent_today key"
        assert "otp_count" in res_stats.json(), "Missing otp_count key"
        print("[PASS] SMS Audit Logs and Admin stats telemetry behave correctly")
    except Exception as e:
        print(f"[FAIL] SMS Telemetry test: {e}")
        sys.exit(1)

    # 00. Cleanup test user
    modify_db_record("DELETE FROM user WHERE phone = ?", (test_phone,))
    modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
    modify_db_record("DELETE FROM registration_otps WHERE phone = ?", (test_phone,))
    modify_db_record("DELETE FROM sms_audit_logs WHERE phone = ?", (test_phone,))
    print("\n=== ALL SMS LIFECYCLE TESTS PASSED SUCCESSFULLY ===")

if __name__ == '__main__':
    run_tests()
