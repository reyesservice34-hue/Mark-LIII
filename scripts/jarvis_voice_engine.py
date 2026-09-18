#!/usr/bin/env python3
"""
JARVIS Voice Output Engine
Text-to-Speech with iconic JARVIS voice characteristics
Provides voice responses via WhatsApp and other channels
"""

import os
import json
import logging
from typing import Dict, Optional, List
from datetime import datetime
from pathlib import Path
import subprocess
import requests
from enum import Enum

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VoiceProvider(Enum):
    """Available TTS providers."""
    GOOGLE_TTS = "google"  # Free, good quality
    ELEVENLABS = "elevenlabs"  # Best quality, paid
    AWS_POLLY = "aws_polly"  # Professional, paid
    ESPEAK = "espeak"  # Local, free (offline)
    PYTTSX3 = "pyttsx3"  # Local Python, free (offline)


class JARVISVoiceEngine:
    """Text-to-Speech engine with JARVIS voice characteristics."""

    # JARVIS Voice Characteristics (from Iron Man films)
    # Actor: Paul Bettany
    # Characteristics: British English, calm, sophisticated, slightly formal
    JARVIS_VOICE_PROFILE = {
        "name": "JARVIS",
        "actor": "Paul Bettany",
        "language": "en-GB",  # British English
        "style": "formal",
        "tone": "calm, sophisticated, professional",
        "pitch": 1.0,  # Neutral pitch
        "speed": 0.95,  # Slightly slower for clarity
        "accent": "British",
        "personality": "Respectful, intelligent, slightly witty"
    }

    def __init__(self):
        self.memory_file = Path("jarvis_voice_configuration.json")
        self.provider = self._detect_provider()
        self.history_file = Path("jarvis_voice_history.json")
        self._load_configuration()
        self._load_history()
        logger.info(f"🎙️  JARVIS Voice Engine initialized with provider: {self.provider.value}")

    def _detect_provider(self) -> VoiceProvider:
        """Detect available TTS provider."""
        # Try ElevenLabs first (if API key available)
        if os.getenv("ELEVENLABS_API_KEY"):
            return VoiceProvider.ELEVENLABS

        # Try Google TTS
        try:
            import google.cloud.texttospeech
            return VoiceProvider.GOOGLE_TTS
        except ImportError:
            pass

        # Try pyttsx3 (local, works offline)
        try:
            import pyttsx3
            return VoiceProvider.PYTTSX3
        except ImportError:
            pass

        # Fallback to espeak
        return VoiceProvider.ESPEAK

    def _load_configuration(self):
        """Load or create JARVIS voice configuration."""
        if self.memory_file.exists():
            with open(self.memory_file) as f:
                self.config = json.load(f)
        else:
            self.config = {
                "jarvis_profile": self.JARVIS_VOICE_PROFILE,
                "provider": self.provider.value,
                "settings": {
                    "language": "en-GB",
                    "pitch": 1.0,
                    "speed": 0.95,
                    "volume": 1.0
                },
                "cache_directory": "jarvis_voice_cache",
                "created": datetime.now().isoformat()
            }
            self._save_configuration()

    def _save_configuration(self):
        """Save voice configuration."""
        with open(self.memory_file, "w") as f:
            json.dump(self.config, f, indent=2)

    def _load_history(self):
        """Load voice output history."""
        if self.history_file.exists():
            with open(self.history_file) as f:
                self.history = json.load(f)
        else:
            self.history = {"voice_outputs": []}

    def _save_history(self):
        """Save voice output history."""
        with open(self.history_file, "w") as f:
            json.dump(self.history, f, indent=2)

    def text_to_speech(self, text: str, voice_id: Optional[str] = None) -> Dict:
        """Convert text to speech with JARVIS voice characteristics."""

        logger.info(f"🎙️  Converting to speech: {text[:50]}...")

        voice_id = voice_id or "jarvis"

        # Route to appropriate provider
        if self.provider == VoiceProvider.ELEVENLABS:
            audio_file = self._tts_elevenlabs(text)
        elif self.provider == VoiceProvider.GOOGLE_TTS:
            audio_file = self._tts_google(text)
        elif self.provider == VoiceProvider.PYTTSX3:
            audio_file = self._tts_pyttsx3(text)
        else:
            audio_file = self._tts_espeak(text)

        # Store in history
        entry = {
            "timestamp": datetime.now().isoformat(),
            "text": text,
            "voice_id": voice_id,
            "provider": self.provider.value,
            "audio_file": audio_file,
            "file_size_kb": os.path.getsize(audio_file) // 1024 if os.path.exists(audio_file) else 0
        }
        self.history["voice_outputs"].append(entry)
        self._save_history()

        logger.info(f"✅ Voice output created: {audio_file}")

        return {
            "status": "ok",
            "text": text,
            "audio_file": audio_file,
            "provider": self.provider.value,
            "voice_profile": self.JARVIS_VOICE_PROFILE,
            "timestamp": datetime.now().isoformat()
        }

    def _tts_elevenlabs(self, text: str) -> str:
        """ElevenLabs TTS (best quality)."""
        try:
            api_key = os.getenv("ELEVENLABS_API_KEY")
            if not api_key:
                logger.warning("⚠️  ElevenLabs API key not found, falling back to pyttsx3")
                return self._tts_pyttsx3(text)

            # ElevenLabs voice: Try to find British male voice similar to Paul Bettany
            # Using voice_id for British male voice
            voice_id = "TxGEqnHWrfWFTfGW9XjX"  # British male voice

            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

            headers = {
                "xi-api-key": api_key,
                "Content-Type": "application/json"
            }

            data = {
                "text": text,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }

            response = requests.post(url, json=data, headers=headers, timeout=30)

            if response.status_code == 200:
                audio_file = f"jarvis_voice_cache/jarvis_elevenlabs_{datetime.now().timestamp()}.mp3"
                os.makedirs("jarvis_voice_cache", exist_ok=True)
                with open(audio_file, "wb") as f:
                    f.write(response.content)
                return audio_file
            else:
                logger.warning(f"⚠️  ElevenLabs error: {response.status_code}, falling back")
                return self._tts_pyttsx3(text)

        except Exception as e:
            logger.warning(f"⚠️  ElevenLabs error: {e}, falling back to pyttsx3")
            return self._tts_pyttsx3(text)

    def _tts_google(self, text: str) -> str:
        """Google Cloud TTS."""
        try:
            from google.cloud import texttospeech

            client = texttospeech.TextToSpeechClient()

            synthesis_input = texttospeech.SynthesisInput(text=text)

            voice = texttospeech.VoiceSelectionParams(
                language_code="en-GB",
                name="en-GB-Standard-B",  # British male voice
                ssml_gender=texttospeech.SsmlVoiceGender.MALE
            )

            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=0.95,
                pitch=0.0
            )

            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config
            )

            audio_file = f"jarvis_voice_cache/jarvis_google_{datetime.now().timestamp()}.mp3"
            os.makedirs("jarvis_voice_cache", exist_ok=True)
            with open(audio_file, "wb") as f:
                f.write(response.audio_content)

            return audio_file

        except Exception as e:
            logger.warning(f"⚠️  Google TTS error: {e}, falling back to pyttsx3")
            return self._tts_pyttsx3(text)

    def _tts_pyttsx3(self, text: str) -> str:
        """Local pyttsx3 TTS (offline, free)."""
        try:
            import pyttsx3

            engine = pyttsx3.init()

            # Set voice properties for JARVIS-like sound
            engine.setProperty('rate', 150)  # Slower speech rate
            engine.setProperty('volume', 1.0)

            # Try to set British voice if available
            voices = engine.getProperty('voices')
            british_voice = None
            for voice in voices:
                if 'en_GB' in voice.id or 'British' in voice.name:
                    british_voice = voice.id
                    break

            if british_voice:
                engine.setProperty('voice', british_voice)

            audio_file = f"jarvis_voice_cache/jarvis_pyttsx3_{datetime.now().timestamp()}.mp3"
            os.makedirs("jarvis_voice_cache", exist_ok=True)

            engine.save_to_file(text, audio_file)
            engine.runAndWait()

            return audio_file

        except Exception as e:
            logger.warning(f"⚠️  pyttsx3 error: {e}, falling back to espeak")
            return self._tts_espeak(text)

    def _tts_espeak(self, text: str) -> str:
        """Local espeak TTS (offline, free, system utility)."""
        try:
            audio_file = f"jarvis_voice_cache/jarvis_espeak_{datetime.now().timestamp()}.wav"
            os.makedirs("jarvis_voice_cache", exist_ok=True)

            cmd = [
                "espeak",
                "-v", "en-GB",  # British English
                "-s", "150",     # Speed (words per minute)
                "-w", audio_file,
                text
            ]

            subprocess.run(cmd, check=True, capture_output=True)

            return audio_file

        except Exception as e:
            logger.error(f"❌ espeak error: {e}")
            logger.info("Install espeak: apt-get install espeak")
            return None

    def get_voice_profile(self) -> Dict:
        """Get JARVIS voice profile information."""
        return {
            "name": "JARVIS",
            "profile": self.JARVIS_VOICE_PROFILE,
            "provider": self.provider.value,
            "settings": self.config["settings"],
            "characteristics": {
                "actor": "Paul Bettany",
                "source": "Iron Man films",
                "accent": "British English",
                "personality": "Sophisticated, respectful, intelligent"
            }
        }

    def get_voice_history(self, limit: int = 10) -> List[Dict]:
        """Get recent voice outputs."""
        return self.history["voice_outputs"][-limit:]

    def list_available_providers(self) -> List[str]:
        """List available TTS providers."""
        providers = []

        # Check each provider
        if os.getenv("ELEVENLABS_API_KEY"):
            providers.append("ElevenLabs (premium quality)")

        try:
            import google.cloud.texttospeech
            providers.append("Google Cloud TTS (high quality)")
        except ImportError:
            pass

        try:
            import pyttsx3
            providers.append("pyttsx3 (local, offline)")
        except ImportError:
            pass

        try:
            subprocess.run(["espeak", "--version"], capture_output=True, check=True)
            providers.append("espeak (local, offline)")
        except:
            pass

        return providers if providers else ["No TTS providers available"]


def main():
    """CLI interface for voice engine."""
    print("\n" + "="*70)
    print("🎙️  JARVIS VOICE OUTPUT ENGINE")
    print("="*70 + "\n")

    engine = JARVISVoiceEngine()

    # Show profile
    profile = engine.get_voice_profile()
    print(f"🎭 Voice Profile: {profile['name']}")
    print(f"   Actor: {profile['characteristics']['actor']}")
    print(f"   Source: {profile['characteristics']['source']}")
    print(f"   Accent: {profile['characteristics']['accent']}")
    print(f"   Personality: {profile['characteristics']['personality']}")
    print()

    # Show provider
    print(f"🔊 TTS Provider: {engine.provider.value}")
    print(f"   Available: {', '.join(engine.list_available_providers())}")
    print()

    # Test message
    print("📝 Test Message:")
    test_text = "Good morning, sir. I am JARVIS. How may I assist you today?"
    print(f"   '{test_text}'")
    print()

    print("🎙️  Converting to speech...")
    result = engine.text_to_speech(test_text)

    if result["audio_file"]:
        print(f"✅ Audio file created: {result['audio_file']}")
        file_size = os.path.getsize(result["audio_file"]) // 1024
        print(f"   Size: {file_size} KB")
        print(f"   Provider: {result['provider']}")
    else:
        print("❌ Failed to create audio file")

    print()
    print("="*70)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Convert text from command line
        text = " ".join(sys.argv[1:])
        engine = JARVISVoiceEngine()
        result = engine.text_to_speech(text)

        print(f"\n✅ Audio: {result['audio_file']}\n")
    else:
        main()
