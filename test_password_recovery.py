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
    print("=== STARTING ADVANCED PASSWORD RECOVERY SECURITY TESTS ===")

    # Ensure a test user exists in the local database
    # Phone: 7777777777 (valid Indian format starting with 7)
    test_phone = "7777777777"
    dummy_uid = "recovery-test-uid-123"
    
    # 0. Setup: Clean up any old test records & insert test user
    modify_db_record("DELETE FROM user WHERE phone = ?", (test_phone,))
    modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
    
    modify_db_record(
        'INSERT INTO user (firebase_uid, name, email, phone, role, is_buyer, is_farmer, is_admin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (dummy_uid, "Recovery Tester", "tester@naadan.com", test_phone, "buyer", 1, 0, 0)
    )
    print("[SETUP] Created recovery test user.")

    # 1. Indian Phone Validation
    try:
        invalid_phones = ["12345", "9497856", "5497856550"] # 5... is invalid Indian starting digit
        for iphone in invalid_phones:
            res = requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": iphone})
            print(f"[TEST] Invalid phone ({iphone}) response status: {res.status_code}")
            assert res.status_code == 400, f"Invalid phone {iphone} should return 400"
            assert "Indian phone number" in res.json().get("msg", ""), "Expected Indian validation message"
        print("[PASS] Indian phone validation behaves correctly")
    except Exception as e:
        print(f"[FAIL] Indian phone validation test: {e}")
        sys.exit(1)

    # 2. OTP Cooldown (60 seconds)
    try:
        # Request 1: Should succeed
        res1 = requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})
        print(f"[TEST] First request status: {res1.status_code}")
        assert res1.status_code == 200, "First request failed"

        # Request 2 (immediate): Should trigger cooldown 429
        res2 = requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})
        print(f"[TEST] Second request (immediate) status: {res2.status_code}, response: {res2.json()}")
        assert res2.status_code == 429, "Immediate repeat request should trigger 429 Cooldown"
        assert "wait 60 seconds" in res2.json().get("msg", ""), "Expected cooldown warning message"
        print("[PASS] OTP Cooldown (60 seconds) behaves correctly")
    except Exception as e:
        print(f"[FAIL] OTP Cooldown test: {e}")
        sys.exit(1)

    # 3. OTP Expiry (5 minutes)
    try:
        # Manually clear cooldown in DB to make another request
        modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
        
        # Request new OTP
        requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})
        
        # Modify the created OTP record in SQLite to expire it (set to 10 minutes ago)
        expired_time = (datetime.utcnow() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S.%f")
        modify_db_record("UPDATE password_reset SET otp_expires_at = ? WHERE phone = ?", (expired_time, test_phone))
        
        # Attempt verification with dummy OTP (value doesn't matter since it's expired)
        res = requests.post(f"{BASE_URL}/auth/verify-recovery-otp", json={"phone": test_phone, "otp": "123456"})
        print(f"[TEST] Verify expired OTP status: {res.status_code}, response: {res.json()}")
        assert res.status_code == 400, "Expired OTP should return 400"
        assert "expired otp" in res.json().get("msg", "").lower(), "Expected expiry error message"
        print("[PASS] OTP Expiration behaves correctly")
    except Exception as e:
        print(f"[FAIL] OTP Expiry test: {e}")
        sys.exit(1)

    # 4. OTP Verification Lockout (5 attempts)
    try:
        # Clear old resets & request a new OTP
        modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
        requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})

        # Submit incorrect OTPs 5 times
        for attempt in range(1, 6):
            res = requests.post(f"{BASE_URL}/auth/verify-recovery-otp", json={"phone": test_phone, "otp": "000000"})
            print(f"[TEST] Failed attempt {attempt} status: {res.status_code}")
            assert res.status_code == 400, "Failed attempt should return 400"
            assert "expired otp" in res.json().get("msg", "").lower(), "Expected generic incorrect OTP message"

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
        # Clear old resets & request a new OTP
        modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
        requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})

        # Extract the raw OTP from otp_log.txt to perform successful verification
        otp_val = None
        log_path = os.path.join(os.path.dirname(__file__), "otp_log.txt")
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                log_lines = f.readlines()
                for line in reversed(log_lines):
                    if test_phone in line:
                        # Extract the 6 digit OTP from line
                        parts = line.strip().split("OTP: ")
                        if len(parts) > 1:
                            otp_val = parts[1].strip()
                            break
        
        assert otp_val is not None, "Failed to capture generated OTP from log file"
        print(f"[TEST] Captured OTP from log: {otp_val}")

        # Verify OTP to get reset_token
        res_verify = requests.post(f"{BASE_URL}/auth/verify-recovery-otp", json={"phone": test_phone, "otp": otp_val})
        assert res_verify.status_code == 200, "Verification with correct OTP failed"
        reset_token = res_verify.json()["reset_token"]
        print(f"[TEST] Captured reset_token: {reset_token}")

        # Try weak passwords
        weak_pwds = [
            "12345",         # Too short
            "abcdefgh",      # No uppercase, no number
            "Abcdefgh",      # No number
            "12345678",      # No letters
            "Abcdefg1"       # Valid (8 chars, 1 upper, 1 lower, 1 number)
        ]
        
        for idx, pwd in enumerate(weak_pwds[:-1]):
            res_reset = requests.post(f"{BASE_URL}/auth/reset-password", json={
                "phone": test_phone,
                "reset_token": reset_token,
                "new_password": pwd
            })
            print(f"[TEST] Reset with weak password '{pwd}' status: {res_reset.status_code}, response: {res_reset.json()}")
            assert res_reset.status_code == 400, f"Password {pwd} should be rejected"
            assert "contain at least one uppercase" in res_reset.json().get("msg", ""), "Expected password validation message"
            
        # Try valid password
        valid_pwd = weak_pwds[-1]
        res_reset_valid = requests.post(f"{BASE_URL}/auth/reset-password", json={
            "phone": test_phone,
            "reset_token": reset_token,
            "new_password": valid_pwd
        })
        print(f"[TEST] Reset with valid password status: {res_reset_valid.status_code}")
        assert res_reset_valid.status_code == 200, "Reset with valid password failed"
        print("[PASS] Strong password validation behaves correctly")
    except Exception as e:
        print(f"[FAIL] Strong password validation test: {e}")
        sys.exit(1)

    # 6. Automatic Cleanup
    try:
        # Inject an expired unused record and a used record older than 24 hours
        expired_unused_time = (datetime.utcnow() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S.%f")
        used_old_time = (datetime.utcnow() - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S.%f")
        
        # Inject expired record
        modify_db_record(
            "INSERT INTO password_reset (phone, otp_hash, otp_expires_at, is_used, created_at) VALUES (?, ?, ?, ?, ?)",
            ("9999999999", "dummy_hash", expired_unused_time, 0, expired_unused_time)
        )
        # Inject old used record
        modify_db_record(
            "INSERT INTO password_reset (phone, otp_hash, otp_expires_at, is_used, created_at) VALUES (?, ?, ?, ?, ?)",
            ("9999999999", "dummy_hash", expired_unused_time, 1, used_old_time)
        )
        
        # Trigger cleanup by requesting an OTP cooldown
        requests.post(f"{BASE_URL}/auth/forgot-password", json={"phone": test_phone})
        
        # Check SQLite db directly if these records were deleted
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM password_reset WHERE phone = '9999999999'")
        records = cursor.fetchall()
        conn.close()
        
        print(f"[TEST] Records remaining after cleanup: {len(records)}")
        assert len(records) == 0, "Pruned records should be deleted by automatic cleanup"
        print("[PASS] Automatic database cleanup behaves correctly")
    except Exception as e:
        print(f"[FAIL] Automatic cleanup test: {e}")
        sys.exit(1)

    # Cleanup test user
    modify_db_record("DELETE FROM user WHERE phone = ?", (test_phone,))
    modify_db_record("DELETE FROM password_reset WHERE phone = ?", (test_phone,))
    print("[CLEANUP] Cleaned up recovery test user records.")
    
    print("\n=== ALL SECURITY AND SAFES PASSWORD RECOVERY TESTS PASSED ===")

if __name__ == '__main__':
    run_tests()
