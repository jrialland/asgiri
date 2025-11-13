"""
SSL/TLS utilities for asgiri server.

This module provides functions to generate self-signed certificates
and manage SSL contexts for HTTPS support.
"""

import datetime
import ipaddress
import ssl
from pathlib import Path
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtensionOID, NameOID
from loguru import logger


def generate_self_signed_cert(
    hostname: str = "localhost",
    ip_addresses: list[str] | None = None,
    key_size: int = 2048,
    validity_days: int = 365,
) -> Tuple[bytes, bytes]:
    """
    Generate a self-signed certificate and private key.

    Args:
        hostname: The hostname to use in the certificate (default: localhost)
        ip_addresses: List of IP addresses to include in Subject Alternative Names
        key_size: Size of the RSA key in bits (default: 2048)
        validity_days: Number of days the certificate is valid (default: 365)

    Returns:
        Tuple of (certificate_pem, private_key_pem) as bytes
    """
    logger.info(f"Generating self-signed certificate for {hostname}")

    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=key_size, backend=default_backend()
    )

    # Build the subject and issuer (same for self-signed)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Self-Signed"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Localhost"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Asgiri Dev Server"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ]
    )

    # Build Subject Alternative Names (SAN)
    san_list: list[x509.GeneralName] = [x509.DNSName(hostname)]

    # Add IP addresses if provided
    if ip_addresses:
        for ip_addr in ip_addresses:
            try:
                san_list.append(x509.IPAddress(ipaddress.ip_address(ip_addr)))
            except ValueError:
                logger.warning(f"Invalid IP address: {ip_addr}, skipping")

    # Always add localhost variations
    if hostname != "localhost":
        san_list.append(x509.DNSName("localhost"))
    san_list.append(x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")))
    san_list.append(x509.IPAddress(ipaddress.IPv6Address("::1")))

    # Create certificate
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(
            datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=validity_days)
        )
        .add_extension(
            x509.SubjectAlternativeName(san_list),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [
                    x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                ]
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256(), default_backend())
    )

    # Serialize to PEM format
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    logger.info(f"Certificate generated successfully (valid for {validity_days} days)")

    return cert_pem, key_pem


def save_cert_and_key(
    cert_pem: bytes,
    key_pem: bytes,
    cert_path: str | Path = "server.crt",
    key_path: str | Path = "server.key",
) -> Tuple[Path, Path]:
    """
    Save certificate and private key to files.

    Args:
        cert_pem: Certificate in PEM format
        key_pem: Private key in PEM format
        cert_path: Path to save the certificate
        key_path: Path to save the private key

    Returns:
        Tuple of (cert_path, key_path) as Path objects
    """
    cert_path = Path(cert_path)
    key_path = Path(key_path)

    # Write certificate
    cert_path.write_bytes(cert_pem)
    logger.info(f"Certificate saved to {cert_path}")

    # Write private key with restricted permissions
    key_path.write_bytes(key_pem)
    # On Unix systems, set restrictive permissions (owner read/write only)
    try:
        key_path.chmod(0o600)
    except (OSError, AttributeError, NotImplementedError) as e:
        # Windows doesn't support chmod the same way, or permissions may fail
        logger.debug(f"Could not set permissions on {key_path}: {e}")
    logger.info(f"Private key saved to {key_path}")

    return cert_path, key_path


def create_ssl_context(
    certfile: str | Path | None = None,
    keyfile: str | Path | None = None,
    cert_data: bytes | None = None,
    key_data: bytes | None = None,
) -> ssl.SSLContext:
    """
    Create an SSL context for the server.

    Either provide file paths (certfile/keyfile) or raw data (cert_data/key_data).

    Args:
        certfile: Path to the certificate file
        keyfile: Path to the private key file
        cert_data: Certificate data in PEM format
        key_data: Private key data in PEM format

    Returns:
        Configured SSL context

    Raises:
        ValueError: If neither file paths nor data are provided
    """
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)

    # Configure ALPN for HTTP/3, HTTP/2, and HTTP/1.1
    # Note: HTTP/3 uses QUIC which has its own ALPN negotiation, but we advertise it here
    # for completeness. The order matters - clients will prefer the first matching protocol.
    context.set_alpn_protocols(["h2", "http/1.1"])

    # Modern TLS configuration
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_3

    if certfile and keyfile:
        # Load from files
        context.load_cert_chain(str(certfile), str(keyfile))
        logger.info(f"Loaded certificate from {certfile} and key from {keyfile}")
    elif cert_data and key_data:
        # Load from memory
        import tempfile

        # We need to use temporary files because ssl.SSLContext doesn't support
        # loading from memory directly in a straightforward way
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".crt"
        ) as cert_tmp:
            cert_tmp.write(cert_data)
            cert_tmp_path = cert_tmp.name

        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".key"
        ) as key_tmp:
            key_tmp.write(key_data)
            key_tmp_path = key_tmp.name

        try:
            context.load_cert_chain(cert_tmp_path, key_tmp_path)
            logger.info("Loaded certificate and key from memory")
        finally:
            # Clean up temporary files
            import os

            try:
                os.unlink(cert_tmp_path)
                os.unlink(key_tmp_path)
            except (OSError, FileNotFoundError) as e:
                # File may already be deleted or permission denied
                logger.debug(f"Could not delete temp files: {e}")
    else:
        raise ValueError(
            "Either certfile/keyfile or cert_data/key_data must be provided"
        )

    return context
