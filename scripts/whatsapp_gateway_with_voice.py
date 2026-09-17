#!/usr/bin/env python3
"""
WhatsApp Gateway with Voice Output
Sends text AND voice responses via WhatsApp
"""

import os
import json
import logging
from typing import Dict, Optional
from datetime import datetime
from pathlib import Path
import requests
from flask import Flask, request
from dotenv import load_dotenv

# Import voice engine
import sys
sys.path.insert(0, str(Path(__file__).parent))
from jarvis_voice_engine import JARVISVoiceEngine

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


class WhatsAppGatewayWithVoice:
    """WhatsApp gateway with integrated voice output."""

    def __init__(self):
        self.twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.twilio_whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155552671")
        self.jarvis_coordinator_url = os.getenv("JARVIS_COORDINATOR_URL", "http://localhost:8000")

        self.voice_engine = JARVISVoiceEngine()
        self.message_history_file = Path("whatsapp_message_history.json")
        self.session = requests.Session()

        self._load_message_history()

    def _load_message_history(self):
        """Load message history."""
        if self.message_history_file.exists():
            with open(self.message_history_file, encoding='utf-8') as f:
                self.history = json.load(f)
        else:
            self.history = {"messages": [], "conversations": {}}

    def _save_message_history(self):
        """Save message history."""
        with open(self.message_history_file, "w", encoding='utf-8') as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

    def send_text_message(self, to_number: str, message: str) -> bool:
        """Send text message via Twilio."""
        if not self.twilio_account_sid or not self.twilio_auth_token:
            logger.info("ℹ️  Twilio not configured - simulating message send")
            logger.info(f"   To: {to_number}")
            logger.info(f"   Message: {message[:100]}")
            return True

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
                logger.info(f"✅ Text message sent to {to_number}")
                return True
            else:
                logger.error(f"❌ Twilio error: {resp.status_code}")
                return False

        except Exception as e:
            logger.error(f"❌ Failed to send message: {e}")
            return False

    def send_voice_message(self, to_number: str, text: str) -> bool:
        """
        Send voice message via WhatsApp.
        Converts text to speech and sends as audio message.
        """
        try:
            logger.info(f"🎙️  Creating voice message for {to_number}...")

            # Generate voice
            voice_result = self.voice_engine.text_to_speech(text)

            if not voice_result["audio_file"] or not os.path.exists(voice_result["audio_file"]):
                logger.warning("⚠️  Voice generation failed, sending text instead")
                return self.send_text_message(to_number, f"🎙️ [Voice message]\n{text}")

            # If Twilio not configured, just show what would be sent
            if not self.twilio_account_sid or not self.twilio_auth_token:
                logger.info(f"ℹ️  Twilio not configured - simulating voice send")
                logger.info(f"   To: {to_number}")
                logger.info(f"   Audio: {voice_result['audio_file']}")
                logger.info(f"   Text: {text[:100]}")
                return True

            # Upload audio to Twilio
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Messages.json"

            with open(voice_result["audio_file"], "rb") as f:
                files = {"MediaUrl": (voice_result["audio_file"], f, "audio/mpeg")}
                data = {
                    "From": self.twilio_whatsapp_number,
                    "To": f"whatsapp:{to_number}",
                    "Body": "🎙️ JARVIS Voice Message"
                }

                resp = self.session.post(
                    url,
                    data=data,
                    files=files,
                    auth=(self.twilio_account_sid, self.twilio_auth_token),
                    timeout=30
                )

            if resp.status_code == 201:
                logger.info(f"✅ Voice message sent to {to_number}")
                return True
            else:
                logger.warning(f"⚠️  Voice send failed: {resp.status_code}, sending text")
                return self.send_text_message(to_number, text)

        except Exception as e:
            logger.error(f"❌ Voice message error: {e}")
            logger.info("   Falling back to text message")
            return self.send_text_message(to_number, text)

    def process_incoming_message(self, from_number: str, message_text: str) -> tuple:
        """
        Process incoming message and return text + voice response.
        Returns: (text_response, voice_response_file)
        """
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
        text_response = self._delegate_to_coordinator(from_number, message_text)

        # Generate voice response
        voice_result = self.voice_engine.text_to_speech(text_response)
        voice_file = voice_result["audio_file"] if voice_result else None

        return text_response, voice_file

    def _delegate_to_coordinator(self, user_number: str, instruction: str) -> str:
        """Send to JARVIS Coordinator."""
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
                logger.info(f"✅ Coordinator response ready")
                return response_text
            else:
                logger.error(f"❌ Coordinator error: {resp.status_code}")
                return "❌ Fehler bei der Verarbeitung. Bitte versuche es erneut."

        except Exception as e:
            logger.error(f"❌ Coordinator connection failed: {e}")
            return f"❌ Verbindungsfehler: {str(e)}"

    def get_voice_profile(self) -> Dict:
        """Get JARVIS voice profile."""
        return self.voice_engine.get_voice_profile()


gateway = WhatsAppGatewayWithVoice()


@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    """Receive WhatsApp messages and send text+voice responses."""
    try:
        data = request.form.to_dict()

        from_number = data.get("From", "").replace("whatsapp:", "")
        message_text = data.get("Body", "")

        if not message_text:
            return {"status": "ok"}, 200

        # Process message
        text_response, voice_file = gateway.process_incoming_message(from_number, message_text)

        # Send text response
        gateway.send_text_message(from_number, text_response)

        # Send voice response
        if voice_file:
            gateway.send_voice_message(from_number, text_response)

        return {"status": "ok"}, 200

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return {"error": str(e)}, 500


@app.route("/whatsapp/test", methods=["POST"])
def test_whatsapp():
    """Test WhatsApp with voice (no Twilio needed)."""
    try:
        data = request.get_json()
        from_number = data.get("from_number", "1234567890")
        message_text = data.get("message", "Test message")
        send_voice = data.get("voice", True)

        text_response, voice_file = gateway.process_incoming_message(from_number, message_text)

        return {
            "status": "ok",
            "from": from_number,
            "message": message_text,
            "text_response": text_response,
            "voice_file": voice_file if send_voice else None,
            "voice_available": send_voice and bool(voice_file),
            "timestamp": datetime.now().isoformat()
        }, 200

    except Exception as e:
        logger.error(f"❌ Test error: {e}")
        return {"error": str(e)}, 500


@app.route("/whatsapp/voice_profile", methods=["GET"])
def voice_profile():
    """Get JARVIS voice profile."""
    return gateway.get_voice_profile(), 200


@app.route("/health", methods=["GET"])
def health():
    """Health check."""
    return {
        "status": "ok",
        "service": "WhatsApp Gateway with Voice",
        "voice_engine": "ACTIVE",
        "voice_provider": gateway.voice_engine.provider.value,
        "timestamp": datetime.now().isoformat()
    }, 200


def main():
    """Start WhatsApp gateway with voice."""
    print("\n" + "="*70)
    print("📱🎙️  WHATSAPP GATEWAY WITH VOICE OUTPUT")
    print("="*70 + "\n")

    profile = gateway.get_voice_profile()
    print(f"🎭 JARVIS Voice Profile:")
    print(f"   Name: {profile['name']}")
    print(f"   Actor: {profile['characteristics']['actor']}")
    print(f"   Accent: {profile['characteristics']['accent']}")
    print(f"   Personality: {profile['characteristics']['personality']}")
    print(f"\n🔊 TTS Provider: {gateway.voice_engine.provider.value}")
    print(f"   Available: {', '.join(gateway.voice_engine.list_available_providers())}")
    print()

    print("📱 WhatsApp Features:")
    print("   ✅ Text messages")
    print("   ✅ Voice messages (via TTS)")
    print("   ✅ JARVIS voice characteristics")
    print("   ✅ Message history tracking")
    print()

    print("🔗 Endpoints:")
    print("   POST /whatsapp/webhook       - Receive WhatsApp messages")
    print("   POST /whatsapp/test          - Test without Twilio")
    print("   GET  /whatsapp/voice_profile - Get voice info")
    print("   GET  /health                 - Health check")
    print()

    print("🎙️  Response Modes:")
    print("   📝 Text: Always sent")
    print("   🎙️  Voice: Automatically generated and sent")
    print()

    print("="*70)
    print("🚀 Starting WhatsApp Gateway with Voice on :5000")
    print("="*70 + "\n")

    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
