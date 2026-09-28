#!/usr/bin/env python3
"""
WhatsApp Gateway for JARVIS Autonomous Coordinator
Enables two-way communication via WhatsApp (Twilio API)
"""

import os
import json
import logging
from typing import Dict, Optional, List
from datetime import datetime
from pathlib import Path
import requests
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


class WhatsAppGateway:
    """WhatsApp integration for JARVIS command delegation."""

    def __init__(self):
        self.twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.twilio_whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155552671")
        self.jarvis_coordinator_url = os.getenv("JARVIS_COORDINATOR_URL", "http://localhost:8000")
        self.message_history_file = Path("whatsapp_message_history.json")

        self.session = requests.Session()
        self._load_message_history()

    def _load_message_history(self):
        """Load or initialize message history."""
        if self.message_history_file.exists():
            with open(self.message_history_file) as f:
                self.history = json.load(f)
        else:
            self.history = {"messages": [], "conversations": {}}

    def _save_message_history(self):
        """Save message history to file."""
        with open(self.message_history_file, "w") as f:
            json.dump(self.history, f, indent=2)

    def send_message(self, to_number: str, message: str) -> bool:
        """Send WhatsApp message via Twilio."""
        if not self.twilio_account_sid or not self.twilio_auth_token:
            logger.warning("❌ Twilio credentials not configured")
            return False

        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Messages.json"

            data = {
                "From": self.twilio_whatsapp_number,
                "To": f"whatsapp:{to_number}",
                "Body": message
            }

            resp = self.session.post(
                url,
                data=data,
                auth=(self.twilio_account_sid, self.twilio_auth_token),
                timeout=10
            )

            if resp.status_code == 201:
                logger.info(f"✅ Message sent to {to_number}")
                return True
            else:
                logger.error(f"❌ Twilio error: {resp.status_code}")
                logger.error(f"   Response: {resp.text}")
                return False

        except Exception as e:
            logger.error(f"❌ Failed to send message: {e}")
            return False

    def process_incoming_message(self, from_number: str, message_text: str) -> str:
        """Process incoming WhatsApp message and delegate to JARVIS."""
        logger.info(f"📨 Incoming from {from_number}: {message_text}")

        # Store in history
        msg_entry = {
            "timestamp": datetime.now().isoformat(),
            "from": from_number,
            "message": message_text,
            "type": "incoming"
        }
        self.history["messages"].append(msg_entry)

        if from_number not in self.history["conversations"]:
            self.history["conversations"][from_number] = []
        self.history["conversations"][from_number].append(msg_entry)
        self._save_message_history()

        # Delegate to JARVIS Coordinator
        response = self._delegate_to_coordinator(from_number, message_text)

        return response

    def _delegate_to_coordinator(self, user_number: str, instruction: str) -> str:
        """Send instruction to JARVIS Coordinator for processing."""
        try:
            payload = {
                "user_id": user_number,
                "instruction": instruction,
                "channel": "whatsapp",
                "timestamp": datetime.now().isoformat()
            }

            resp = self.session.post(
                f"{self.jarvis_coordinator_url}/process_instruction",
                json=payload,
                timeout=30
            )

            if resp.status_code == 200:
                result = resp.json()
                response_text = result.get("response", "Ausführung abgeschlossen")
                logger.info(f"✅ Coordinator response: {response_text[:100]}")
                return response_text
            else:
                logger.error(f"❌ Coordinator error: {resp.status_code}")
                return "❌ Fehler bei der Verarbeitung. Bitte versuche es erneut."

        except Exception as e:
            logger.error(f"❌ Coordinator connection failed: {e}")
            return f"❌ Verbindungsfehler: {str(e)}"

    def get_conversation_history(self, user_number: str) -> List[Dict]:
        """Get conversation history for a user."""
        return self.history["conversations"].get(user_number, [])


gateway = WhatsAppGateway()


@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    """Receive incoming WhatsApp messages via Twilio webhook."""
    try:
        data = request.form.to_dict()

        from_number = data.get("From", "").replace("whatsapp:", "")
        message_text = data.get("Body", "")

        if not message_text:
            return {"status": "ok"}, 200

        # Process message
        response = gateway.process_incoming_message(from_number, message_text)

        # Send response back via WhatsApp
        gateway.send_message(from_number, response)

        return {"status": "ok"}, 200

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return {"error": str(e)}, 500


@app.route("/whatsapp/test", methods=["POST"])
def test_whatsapp():
    """Test WhatsApp connectivity without Twilio credentials."""
    try:
        data = request.get_json()
        from_number = data.get("from_number", "1234567890")
        message_text = data.get("message", "Test message")

        response = gateway.process_incoming_message(from_number, message_text)

        return {
            "status": "ok",
            "from": from_number,
            "message": message_text,
            "response": response,
            "timestamp": datetime.now().isoformat()
        }, 200

    except Exception as e:
        logger.error(f"❌ Test error: {e}")
        return {"error": str(e)}, 500


@app.route("/whatsapp/history/<user_number>", methods=["GET"])
def get_history(user_number: str):
    """Retrieve conversation history."""
    try:
        history = gateway.get_conversation_history(user_number)
        return {"history": history}, 200
    except Exception as e:
        logger.error(f"❌ History error: {e}")
        return {"error": str(e)}, 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "WhatsApp Gateway",
        "jarvis_coordinator": "configured",
        "twilio_configured": bool(gateway.twilio_account_sid),
        "timestamp": datetime.now().isoformat()
    }, 200


def main():
    """Start WhatsApp gateway server."""
    print("\n" + "="*60)
    print("🚀 JARVIS WHATSAPP GATEWAY")
    print("="*60 + "\n")

    print("📞 WhatsApp Gateway Configuration:")
    print(f"   ✅ Twilio configured: {bool(gateway.twilio_account_sid)}")
    print(f"   ✅ JARVIS Coordinator: {gateway.jarvis_coordinator_url}")
    print(f"   ✅ Message history: {gateway.message_history_file}")
    print("\n")

    print("🔗 Endpoints:")
    print("   POST /whatsapp/webhook       - Receive WhatsApp messages (Twilio webhook)")
    print("   POST /whatsapp/test          - Test without Twilio (for development)")
    print("   GET  /whatsapp/history/<num> - Get conversation history")
    print("   GET  /health                 - Health check")
    print("\n")

    print("⚙️  To setup:")
    print("   1. Add to .env:")
    print("      TWILIO_ACCOUNT_SID=your_sid")
    print("      TWILIO_AUTH_TOKEN=your_token")
    print("      TWILIO_WHATSAPP_NUMBER=whatsapp:+14155552671")
    print("   2. Configure Twilio webhook to: https://your-domain/whatsapp/webhook")
    print("   3. Start this server: python3 scripts/whatsapp_gateway.py")
    print("\n")

    print("📤 Starting server on :5000...")
    print("="*60 + "\n")

    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
