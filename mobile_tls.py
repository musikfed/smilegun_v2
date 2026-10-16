from __future__ import annotations

import argparse
import ipaddress
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CERT_DIR = ROOT / ".smilegun-certs"
CA_CERT = CERT_DIR / "smilegun-mobile-ca.crt"
CA_KEY = CERT_DIR / "smilegun-mobile-ca.key"
SERVER_CERT = CERT_DIR / "smilegun-mobile-server.crt"
SERVER_KEY = CERT_DIR / "smilegun-mobile-server.key"


def lan_addresses() -> list[str]:
    values = {"127.0.0.1"}
    try:
        values.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        values.add(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    result = []
    for value in values:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            continue
        if ip.version == 4 and (ip.is_private or ip.is_loopback):
            result.append(str(ip))
    return sorted(set(result), key=lambda x: (x.startswith("127."), x))


def _write_private_key(key, path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))


def ensure_mobile_cert() -> tuple[Path, Path, Path, list[str]]:
    """Create a stable local CA and a fresh LAN-IP server certificate.

    The CA is preserved between runs so a phone only needs to trust it once.
    The leaf certificate is regenerated with the current LAN IPv4 addresses.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    if CA_CERT.exists() and CA_KEY.exists():
        ca_key = serialization.load_pem_private_key(CA_KEY.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(CA_CERT.read_bytes())
    else:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SmileGun Local Mobile CA")])
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_encipherment=False, content_commitment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        _write_private_key(ca_key, CA_KEY)
        CA_CERT.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    ips = lan_addresses()
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SmileGun Local Server")])
    sans = [x509.DNSName("localhost")]
    sans.extend(x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips)
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write_private_key(server_key, SERVER_KEY)
    SERVER_CERT.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    return SERVER_CERT, SERVER_KEY, CA_CERT, ips


def main() -> None:
    parser = argparse.ArgumentParser(description="Create SmileGun local HTTPS certificates for phone play.")
    parser.add_argument("--print", action="store_true", dest="print_info")
    args = parser.parse_args()
    cert, key, ca, ips = ensure_mobile_cert()
    if args.print_info:
        print(f"CA certificate: {ca}")
        print("LAN addresses:")
        for ip in ips:
            if not ip.startswith("127."):
                print(f"  https://{ip}:5443")
        print("Install/trust the CA certificate on the phone once, then open the HTTPS address.")


if __name__ == "__main__":
    main()
