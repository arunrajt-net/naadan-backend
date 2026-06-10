import os
import requests
from datetime import datetime
from models import db, SMSAuditLog

class BaseSMSProvider:
    def send_sms(self, phone: str, event_type: str, message_text: str, template_id: str = None, params: dict = None, user_id: int = None) -> bool:
        raise NotImplementedError

class MockProvider(BaseSMSProvider):
    def send_sms(self, phone: str, event_type: str, message_text: str, template_id: str = None, params: dict = None, user_id: int = None) -> bool:
        print(f"\n--- [MOCK SMS SENDER] ---")
        print(f"To: {phone}")
        print(f"Event: {event_type}")
        print(f"Content: {message_text}")
        print(f"Template ID: {template_id}")
        print(f"Params: {params}")
        print(f"--------------------------\n")
        
        # Write to local test file
        log_path = os.path.join(os.path.dirname(__file__), "otp_log.txt")
        try:
            with open(log_path, "a") as f:
                f.write(f"{datetime.utcnow().isoformat()} - Phone: {phone} - Event: {event_type} - Msg: {message_text}\n")
        except Exception as e:
            print("Failed to write to otp_log.txt:", str(e))
            
        # Write to SMS Audit Log
        log_sms_event(phone, event_type, "MOCK", message_text, "MOCK-REF", "SENT", template_id, user_id=user_id)
        return True

class MSG91Provider(BaseSMSProvider):
    def send_sms(self, phone: str, event_type: str, message_text: str, template_id: str = None, params: dict = None, user_id: int = None) -> bool:
        auth_key = os.environ.get("MSG91_AUTH_KEY", "").strip()
        
        # Format phone: Indian numbers require '91' prefix without '+'
        clean_phone = phone[-10:] if len(phone) >= 10 else phone
        full_phone = "91" + clean_phone
        
        if not auth_key:
            print("[MSG91 CONFIG WARNING] MSG91_AUTH_KEY is not set. Automatically falling back to MockProvider.")
            return MockProvider().send_sms(phone, event_type, message_text, template_id, params, user_id)
        
        url = "https://control.msg91.com/api/v5/flow/"
        headers = {
            "authkey": auth_key,
            "Content-Type": "application/json"
        }
        
        # MSG91 Flow payload format
        payload = {
            "template_id": template_id or os.environ.get("MSG91_TEMPLATE_ID"),
            "short_url": "0",
            "recipients": [
                {
                    "mobiles": full_phone,
                    **(params or {})  # Interpolates template variables e.g. {"otp": "123456"}
                }
            ]
        }
        
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=10)
            res_data = res.json()
            print(f"[MSG91 RESPONSE] Status: {res.status_code}, Body: {res_data}")
            if res.status_code == 200 and res_data.get("type") == "success":
                log_sms_event(phone, event_type, "MSG91", message_text, res_data.get("request_id"), "SENT", template_id, user_id)
                return True
            else:
                log_sms_event(phone, event_type, "MSG91", message_text, None, "FAILED", template_id, user_id, str(res_data))
                return False
        except Exception as e:
            print(f"[MSG91 ERROR] Exception during POST: {e}")
            log_sms_event(phone, event_type, "MSG91", message_text, None, "FAILED", template_id, user_id, str(e))
            return False

def log_sms_event(phone, event_type, provider, content, reference, status, template_id=None, user_id=None, error_msg=None):
    try:
        log = SMSAuditLog(
            user_id=user_id,
            phone=phone,
            event_type=event_type,
            provider=provider,
            template_id=template_id,
            message_content=content,
            provider_reference=reference,
            delivery_status=status,
            error_message=error_msg
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print("Failed to save SMS audit log:", str(e))

class ConfigurableSMSProvider(BaseSMSProvider):
    def __init__(self, target_provider: BaseSMSProvider):
        self.target = target_provider

    def send_sms(self, phone: str, event_type: str, message_text: str, template_id: str = None, params: dict = None, user_id: int = None) -> bool:
        # Load enabled events from environment variable
        # Defaults to the essential Phase 1 events
        enabled_env = os.environ.get("SMS_ENABLED_EVENTS", "OTP_RECOVERY,OTP_SIGNUP,NEW_ORDER_ALERT")
        enabled_events = [ev.strip().upper() for ev in enabled_env.split(",") if ev.strip()]
        
        if event_type.upper() not in enabled_events:
            print(f"[SMS COST CONTROL] Event {event_type} is disabled. Skipping SMS sending to {phone}.")
            return True # Return true so the flow isn't interrupted
            
        return self.target.send_sms(phone, event_type, message_text, template_id, params, user_id)

def get_sms_provider() -> BaseSMSProvider:
    provider_type = os.environ.get("SMS_PROVIDER", "MOCK").upper().strip()
    if provider_type == "MSG91":
        provider = MSG91Provider()
    else:
        provider = MockProvider()
    return ConfigurableSMSProvider(provider)
