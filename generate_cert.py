"""
Example: Generate and save self-signed certificates for later use.

This script demonstrates how to:
1. Generate a self-signed certificate
2. Save it to files
3. Use those files with asgiri

After running this script, you can start the server with:
    asgiri --cert server.crt --key server.key --port 8443 tests.app:app
"""
from asgiri.ssl_utils import generate_self_signed_cert, save_cert_and_key

# Generate certificate for localhost
cert_pem, key_pem = generate_self_signed_cert(
    hostname="localhost",
    ip_addresses=["127.0.0.1"],
    validity_days=365
)

# Save to files
cert_path, key_path = save_cert_and_key(
    cert_pem, 
    key_pem,
    cert_path="server.crt",
    key_path="server.key"
)

print(f"Certificate saved to: {cert_path}")
print(f"Private key saved to: {key_path}")
print("\nYou can now start the server with:")
print(f"  asgiri --cert {cert_path} --key {key_path} --port 8443 tests.app:app")
