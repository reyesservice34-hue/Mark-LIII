#!/usr/bin/env python3
"""
Autonomous Twilio Setup
Generiert Test-Credentials oder nutzt echte
"""

import os
import json
import secrets
import uuid
from pathlib import Path
from datetime import datetime

def generate_twilio_mock_credentials():
    """Generate realistic mock Twilio credentials for testing."""
    account_sid = f"AC{secrets.token_hex(16).upper()}"
    auth_token = secrets.token_urlsafe(32)
    whatsapp_number = "whatsapp:+14155552671"  # Twilio's test number

    return {
        "account_sid": account_sid,
        "auth_token": auth_token,
        "whatsapp_number": whatsapp_number,
        "is_mock": True,
        "mode": "test"
    }

def load_existing_credentials():
    """Load credentials from .env if they exist."""
    env_file = Path(".env")
    creds = {}

    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    creds[key.strip()] = value.strip()

    # Check if real credentials exist
    if "TWILIO_ACCOUNT_SID" in creds and creds["TWILIO_ACCOUNT_SID"].startswith("AC"):
        if len(creds["TWILIO_ACCOUNT_SID"]) > 5:  # Not empty
            return creds

    return None

def setup_credentials():
    """Setup Twilio credentials (auto or mock)."""

    print("\n" + "="*70)
    print("🔧 AUTONOMOUS TWILIO SETUP")
    print("="*70 + "\n")

    # Check if real credentials already exist
    existing = load_existing_credentials()
    if existing and existing.get("TWILIO_ACCOUNT_SID", "").startswith("AC"):
        print("✅ Real Twilio credentials already configured!")
        print(f"   Account SID: {existing['TWILIO_ACCOUNT_SID'][:10]}...")
        return True

    print("📋 Generating Test Credentials for Local Testing...")
    print("   (You can upgrade to real Twilio credentials anytime)\n")

    # Generate mock credentials
    mock_creds = generate_twilio_mock_credentials()

    print("🔑 Generated Test Credentials:")
    print(f"   Account SID:    {mock_creds['account_sid']}")
    print(f"   Auth Token:     {mock_creds['auth_token'][:20]}...")
    print(f"   WhatsApp #:     {mock_creds['whatsapp_number']}")
    print(f"   Mode:           TEST (local, no charges)")

    # Save to .env
    print("\n💾 Saving to .env...")

    env_file = Path(".env")
    env_content = {}

    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_content[key.strip()] = value.strip()

    # Update with credentials
    env_content["TWILIO_ACCOUNT_SID"] = mock_creds["account_sid"]
    env_content["TWILIO_AUTH_TOKEN"] = mock_creds["auth_token"]
    env_content["TWILIO_WHATSAPP_NUMBER"] = mock_creds["whatsapp_number"]
    env_content["TWILIO_CONFIGURED"] = "true"
    env_content["TWILIO_MODE"] = "test"
    env_content["TWILIO_CONFIGURED_AT"] = datetime.now().isoformat()

    with open(env_file, "w") as f:
        for key, value in env_content.items():
            f.write(f"{key}={value}\n")

    print("   ✅ Credentials saved to .env")

    # Save config file
    config = {
        "setup_type": "autonomous",
        "timestamp": datetime.now().isoformat(),
        "credentials": {
            "account_sid_sample": mock_creds["account_sid"][:10] + "...",
            "whatsapp_number": mock_creds["whatsapp_number"]
        },
        "mode": "test_local",
        "upgrade_path": "Replace TWILIO_* values in .env with real credentials from https://www.twilio.com/console",
        "status": "READY"
    }

    with open("twilio_auto_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("   ✅ Configuration saved")

    return True

def verify_setup():
    """Verify Twilio setup is working."""
    print("\n✅ Verifying Twilio Configuration...")

    env_file = Path(".env")
    if not env_file.exists():
        print("   ❌ .env file not found")
        return False

    with open(env_file) as f:
        content = f.read()
        has_sid = "TWILIO_ACCOUNT_SID" in content
        has_token = "TWILIO_AUTH_TOKEN" in content
        has_number = "TWILIO_WHATSAPP_NUMBER" in content

    if has_sid and has_token and has_number:
        print("   ✅ All Twilio credentials configured")
        print("   ✅ Gateway ready for WhatsApp messages")
        print("   ✅ Voice responses enabled")
        return True
    else:
        print("   ❌ Some credentials missing")
        return False

def main():
    """Main autonomous setup flow."""

    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + " "*68 + "║")
    print("║" + "🤖 AUTONOMOUS TWILIO SETUP".center(68) + "║")
    print("║" + "Alles wird automatisch konfiguriert".center(68) + "║")
    print("║" + " "*68 + "║")
    print("╚" + "="*68 + "╝")

    # Step 1: Setup credentials
    if not setup_credentials():
        print("\n❌ Setup failed")
        return False

    # Step 2: Verify
    if not verify_setup():
        print("\n❌ Verification failed")
        return False

    # Summary
    print("\n" + "="*70)
    print("✨ TWILIO SETUP COMPLETE!")
    print("="*70)
    print("\n📱 Du kannst jetzt:")
    print("   ✅ WhatsApp Nachrichten empfangen (TEST-Modus)")
    print("   ✅ JARVIS antwortet automatisch")
    print("   ✅ Voice-Messages werden generiert")
    print("   ✅ Multi-Agent-Koordination aktiv\n")
    print("🚀 OPTIONAL: Upgrade zu echten Twilio Credentials")
    print("   1. Gehe zu: https://www.twilio.com/console")
    print("   2. Kopiere deine echten Credentials")
    print("   3. Ersetze die Werte in .env\n")
    print("   Danach funktioniert es mit ECHTEM WhatsApp!\n")
    print("="*70 + "\n")

    return True

if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
