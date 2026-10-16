"""Opt-in: let Python use existing Windows trusted roots during TLS inspection."""
import ssl
from pathlib import Path

import certifi
from dotenv import set_key

root = Path(__file__).resolve().parents[1]
directory = root / "runs"
directory.mkdir(exist_ok=True)
path = directory / "windows-ca.pem"
certificates = []
for raw, encoding, trust in ssl.enum_certificates("ROOT"):
    if encoding == "x509_asn" and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
        certificates.append(ssl.DER_cert_to_PEM_cert(raw))
path.write_text(Path(certifi.where()).read_text() + "\n" + "\n".join(certificates))
set_key(root / ".env", "SSL_CERT_FILE", str(path))
set_key(root / ".env", "REQUESTS_CA_BUNDLE", str(path))
print("SSL_CERT_FILE configured from existing Windows trusted roots. TLS verification stays enabled.")
