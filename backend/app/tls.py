"""Generates (once) a self-signed TLS certificate for local HTTPS.

This lets the bundled dev server actually serve HTTPS out of the box, so the
HSTS header and encrypted transport are not purely theoretical for anyone
running the app locally or on a LAN. A self-signed certificate is NOT
appropriate for a real deployment reachable by patients or the public
internet -- browsers will show a trust warning, and there is no revocation
story. For that case, terminate TLS at a reverse proxy (nginx, Caddy,
Traefik) with a certificate from a real CA (e.g. Let's Encrypt) in front of
this app instead.
"""
from __future__ import annotations

import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def ensure_self_signed_cert(data_dir: Path) -> tuple[Path, Path]:
    tls_dir = data_dir / "tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert_path = tls_dir / "selfsigned.crt"
    key_path = tls_dir / "selfsigned.key"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    return cert_path, key_path
