#!/usr/bin/env python3
"""
Interactive Twilio WhatsApp Setup Wizard
Führt dich Schritt-für-Schritt durch den Setup
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
import webbrowser

class TwilioSetupWizard:
    """Interactive setup wizard for Twilio WhatsApp."""

    def __init__(self):
        self.env_file = Path(".env")
        self.config = {}

    def clear_screen(self):
        """Clear console."""
        os.system("clear" if os.name != "nt" else "cls")

    def print_header(self, title: str):
        """Print formatted header."""
        print("\n" + "="*70)
        print(f"🧙 {title}")
        print("="*70 + "\n")

    def print_step(self, number: int, title: str, description: str = ""):
        """Print formatted step."""
        print(f"\n{'='*70}")
        print(f"📍 SCHRITT {number}: {title}")
        print(f"{'='*70}")
        if description:
            print(f"\n{description}\n")

    def get_input(self, prompt: str, required: bool = True) -> str:
        """Get user input with validation."""
        while True:
            value = input(f"\n➜ {prompt}: ").strip()
            if value or not required:
                return value
            print("⚠️  Dieses Feld ist erforderlich!")

    def step1_open_twilio(self):
        """Step 1: Open Twilio Console."""
        self.print_step(1, "Twilio Console öffnen",
            "Öffne die Twilio Console im Browser")

        print("🔗 URL: https://www.twilio.com/console")
        print("\n✅ Im Browser solltest du sehen:")
        print("   • Account SID (oben links)")
        print("   • Auth Token (rechts)")
        print("   • WhatsApp Sandbox Info")

        response = input("\n➜ Hast du die Console offen? (j/n): ").lower()
        if response != "j":
            print("❌ Abgebrochen.")
            return False
        return True

    def step2_get_account_sid(self) -> str:
        """Step 2: Get Account SID."""
        self.print_step(2, "Account SID kopieren",
            "Auf der Twilio Console (oben links) findest du deine Account SID.\n"
            "Sie sieht so aus: AC_________________ (lange Nummer)\n"
            "👉 Kopiere sie jetzt hier ein:")

        account_sid = self.get_input("Account SID", required=True)

        # Validate
        if not account_sid.startswith("AC"):
            print("⚠️  Warnung: Account SID sollte mit 'AC' anfangen!")
            confirm = input("➜ Trotzdem verwenden? (j/n): ")
            if confirm != "j":
                return self.step2_get_account_sid()

        print(f"✅ Account SID gespeichert: {account_sid[:10]}...")
        return account_sid

    def step3_get_auth_token(self) -> str:
        """Step 3: Get Auth Token."""
        self.print_step(3, "Auth Token kopieren",
            "Auf der Twilio Console (rechts oben) → 'Eye Icon' → Auth Token\n"
            "👉 Kopiere den langen Token hier ein:")

        auth_token = self.get_input("Auth Token", required=True)

        if len(auth_token) < 20:
            print("⚠️  Warnung: Token scheint zu kurz zu sein!")
            confirm = input("➜ Trotzdem verwenden? (j/n): ")
            if confirm != "j":
                return self.step3_get_auth_token()

        print(f"✅ Auth Token gespeichert: {auth_token[:10]}...")
        return auth_token

    def step4_get_whatsapp_number(self) -> str:
        """Step 4: Get WhatsApp Sandbox Number."""
        self.print_step(4, "WhatsApp Sandbox Number kopieren",
            "Auf der Twilio Console → Messaging → Try it out → WhatsApp\n"
            "Du siehst: 'Your sandbox number is: whatsapp:+14155552671'\n"
            "👉 Kopiere diese komplette Nummer hier ein (mit 'whatsapp:'):")

        whatsapp_number = self.get_input("WhatsApp Number", required=True)

        # Validate
        if not whatsapp_number.startswith("whatsapp:"):
            whatsapp_number = f"whatsapp:{whatsapp_number}"
            print(f"✓ Format korrigiert: {whatsapp_number}")

        print(f"✅ WhatsApp Number gespeichert: {whatsapp_number}")
        return whatsapp_number

    def step5_confirm(self, account_sid: str, auth_token: str, whatsapp_number: str) -> bool:
        """Step 5: Confirm all values."""
        self.print_step(5, "Bestätigung",
            "Überprüfe deine Eingaben:\n")

        print(f"📌 Account SID:    {account_sid[:20]}...")
        print(f"📌 Auth Token:     {auth_token[:20]}...")
        print(f"📌 WhatsApp #:     {whatsapp_number}")

        confirm = input("\n➜ Sind diese Werte korrekt? (j/n): ").lower()
        return confirm == "j"

    def step6_save_config(self, account_sid: str, auth_token: str, whatsapp_number: str) -> bool:
        """Step 6: Save configuration."""
        self.print_step(6, "Konfiguration speichern",
            "Speichere die Werte in .env...")

        try:
            # Load existing .env
            env_content = {}
            if self.env_file.exists():
                with open(self.env_file) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            env_content[key.strip()] = value.strip()

            # Update with Twilio config
            env_content["TWILIO_ACCOUNT_SID"] = account_sid
            env_content["TWILIO_AUTH_TOKEN"] = auth_token
            env_content["TWILIO_WHATSAPP_NUMBER"] = whatsapp_number
            env_content["TWILIO_CONFIGURED"] = "true"
            env_content["TWILIO_CONFIGURED_AT"] = datetime.now().isoformat()

            # Write back
            with open(self.env_file, "w") as f:
                for key, value in env_content.items():
                    f.write(f"{key}={value}\n")

            print("✅ .env konfiguriert")

            # Save info
            info = {
                "configured": datetime.now().isoformat(),
                "phone_number": "+49 176 64096121",
                "whatsapp_number": whatsapp_number,
                "status": "READY"
            }

            with open("twilio_configuration.json", "w") as f:
                json.dump(info, f, indent=2)

            print("✅ Konfiguration gespeichert")
            return True

        except Exception as e:
            print(f"❌ Fehler beim Speichern: {e}")
            return False

    def step7_test(self):
        """Step 7: Test configuration."""
        self.print_step(7, "Test durchführen",
            "Starten wir einen Test...\n")

        print("📱 Sende diese Nachricht via WhatsApp an deine Sandbox-Nummer:")
        print("\n   'join jumping-machine'\n")
        print("(oder den 'join [code]' von der Twilio Console)")

        print("\n⏳ Warte auf die Antwort...")
        print("   → Du solltest eine Bestätigung erhalten")
        print("   → Dann kannst du Nachrichten an JARVIS senden!")

        input("\n➜ Drücke ENTER wenn du die Sandbox-Bestätigung erhalten hast...")

        print("\n✅ Setup abgeschlossen!")
        return True

    def run(self):
        """Run the complete setup wizard."""
        self.clear_screen()
        self.print_header("TWILIO WHATSAPP SETUP WIZARD")

        print("Dieser Wizard hilft dir, Twilio zu konfigurieren.\n")
        print("📋 Was du brauchst:")
        print("   ✓ Twilio Account (https://www.twilio.com)")
        print("   ✓ ~5 Minuten Zeit")
        print("   ✓ Dein Handy mit WhatsApp\n")

        confirm = input("➜ Bereit? (j/n): ").lower()
        if confirm != "j":
            print("❌ Abgebrochen.")
            return False

        # Step 1
        if not self.step1_open_twilio():
            return False

        # Step 2-4: Get credentials
        account_sid = self.step2_get_account_sid()
        auth_token = self.step3_get_auth_token()
        whatsapp_number = self.step4_get_whatsapp_number()

        # Step 5: Confirm
        if not self.step5_confirm(account_sid, auth_token, whatsapp_number):
            print("\n⚠️  Wiederhole die Eingabe...\n")
            return self.run()  # Start over

        # Step 6: Save
        if not self.step6_save_config(account_sid, auth_token, whatsapp_number):
            return False

        # Step 7: Test
        self.step7_test()

        # Final summary
        self.print_header("✨ SETUP ABGESCHLOSSEN!")
        print("🎉 JARVIS ist jetzt mit Twilio verbunden!\n")
        print("📱 Du kannst jetzt sagen:")
        print("   • 'Hallo JARVIS'")
        print("   • 'Erstelle einen Preisplan'")
        print("   • 'Analysiere diese Daten'\n")
        print("🎙️  JARVIS antwortet mit TEXT + STIMME!\n")
        print("="*70)

        return True


if __name__ == "__main__":
    wizard = TwilioSetupWizard()
    success = wizard.run()
    sys.exit(0 if success else 1)
