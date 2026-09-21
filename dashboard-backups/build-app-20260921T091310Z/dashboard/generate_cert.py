"""
dashboard/generate_cert.py — one-time self-signed TLS cert for the dashboard.

The dashboard (dashboard/server.py) only serves HTTPS if
config/certs/jarvis.key and jarvis.crt already exist; it never creates them.
Plain HTTP means iOS/Safari refuses microphone access (getUserMedia) and
won't register the PWA service worker — both need a secure context.

Run once on the machine that runs main.py:

    python dashboard/generate_cert.py [hostname_or_ip ...]

Any hostnames/IPs you pass (e.g. jarvis.jarvis-reyes.de, its LAN IP) are
added as Subject Alternative Names so the phone's browser doesn't reject
the cert as issued for the wrong domain. The private key never leaves
this machine and both files are already covered by .gitignore-equivalent
handling in this repo (previous jarvis.key/.crt were removed from git).
"""

import ipaddress
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CERT_DIR = Path(__file__).resolve().parent.parent / "config" / "certs"


def _san_entries(names):
    entries = []
    for n in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            entries.append(x509.DNSName(n))
    return entries


def main(extra_names):
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    key_path = CERT_DIR / "jarvis.key"
    crt_path = CERT_DIR / "jarvis.crt"

    if key_path.exists() or crt_path.exists():
        print(f"[cert] Already present: {crt_path}")
        print("[cert] Delete both files first if you want to regenerate.")
        return

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "jarvis-dashboard"),
    ])
    names = ["localhost", "127.0.0.1", *extra_names]
    san = x509.SubjectAlternativeName(_san_entries(names))

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))   # under the 825-day CA/B max
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    crt_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.chmod(0o600)

    print(f"[cert] Written {crt_path} and {key_path}")
    print(f"[cert] Valid for: {', '.join(names)}")
    print("[cert] Restart main.py — the dashboard will now serve HTTPS.")
    print("[cert] On the iPhone, open the HTTPS port once and accept the "
          "self-signed certificate warning (Safari: 'Show Details' -> "
          "'visit this website').")


if __name__ == "__main__":
    main(sys.argv[1:])
