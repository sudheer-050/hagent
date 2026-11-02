"""Sign a local test MSIX without installing or trusting the test certificate."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import subprocess
import sys
import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def main():
    package = Path(sys.argv[1]).resolve()
    if not package.is_file() or package.suffix != '.msix':
        raise SystemExit('Pass the existing test .msix path')
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Hagent Local Test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CODE_SIGNING]), critical=False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, None, None), critical=True)
            .sign(key, hashes.SHA256()))
    root = Path(__file__).resolve().parents[1]
    private = root / 'build' / 'test-signing.pfx'
    private.parent.mkdir(exist_ok=True)
    password = secrets.token_urlsafe(24)
    private.write_bytes(pkcs12.serialize_key_and_certificates(
        b'Hagent Test', key, cert, None, serialization.BestAvailableEncryption(password.encode())))
    public = package.with_suffix('.cer')
    sdk = Path(os.environ['ProgramFiles(x86)']) / 'Windows Kits/10/bin'
    signer = sorted(sdk.glob('10.*/x64/signtool.exe'), reverse=True)[0]
    try:
        result = subprocess.run([str(signer), 'sign', '/debug', '/a', '/fd', 'SHA256',
                                 '/f', str(private), '/p', password, str(package)])
        if result.returncode:
            raise SystemExit('Test signing failed; see SignTool diagnostics above. No certificate was trusted.')
    finally:
        private.unlink(missing_ok=True)
    public.write_bytes(cert.public_bytes(serialization.Encoding.DER))
    print('Signed for local testing only:', package)
    print('Public test certificate (not installed or trusted):', public)
    print('SHA256 certificate fingerprint:', cert.fingerprint(hashes.SHA256()).hex())


if __name__ == '__main__':
    main()
