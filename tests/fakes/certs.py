"""A throwaway certificate authority and one server certificate from it, for a fake that serves https on the loopback
address (simplefin_server.py). Made anew each time, for one name:

- The authority may sign for that name only (a critical name constraint), and its private key is never written
  anywhere: it is dropped as soon as the server's certificate is signed, so nothing can be signed with it afterwards.
- Only its certificate (public) is written, to `<folder>/ca.pem`, for the one process that should trust the fake; and
  `bundle` writes `<folder>/ca-bundle.pem`, the system's certificates with it, which is what that process's
  SSL_CERT_FILE is set to. The server's own certificate and key are written with owner-only permissions, for ssl to
  load (it takes files), and `Made.forget()` removes them once it has.

Nothing is ever written outside `folder`, which is a run's temporary directory: never the repository or the image."""
from __future__ import annotations

import contextlib
import os
import ssl
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_FILE = "ca.pem"
BUNDLE_FILE = "ca-bundle.pem"


@dataclass(frozen=True)
class Made:
    ca: str     # the authority's certificate: what a client trusts
    cert: str   # the server's certificate and its key, for ssl.SSLContext.load_cert_chain
    key: str

    def forget(self) -> None:
        """Removes the server's certificate and key files (once ssl has loaded them); the authority's certificate stays."""
        for path in (self.cert, self.key):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)


def _write(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # a new file, the owner's only
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def trusting(authority: str | None = None) -> ssl.SSLContext:
    """A client's context that checks certificates against `authority` alone (a throwaway authority's certificate), or
    against the system's when none is given; TLS 1.2 or newer, as the fake serves."""
    context = ssl.create_default_context(cafile=authority)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def bundle(folder: str, authority: str, system: str | None = None) -> str:
    """Writes <folder>/ca-bundle.pem and returns its path: the certificates in `system` (by default the file ssl reads
    the system's from: SSL_CERT_FILE, or OpenSSL's own), when there is one, and then the throwaway authority's."""
    paths = ssl.get_default_verify_paths()
    parts = []
    for source in (system or paths.cafile or paths.openssl_cafile, authority):
        if source and os.path.isfile(source):
            with open(source, "rb") as f:
                parts.append(f.read().strip())
    path = os.path.join(folder, BUNDLE_FILE)
    _write(path, b"\n".join(parts) + b"\n")
    return path


def make(folder: str, name: str, days: tuple[date, ...] = ()) -> Made:
    """A new authority and a certificate for `name` signed by it, in `folder` (which must exist). Valid now and on each
    of `days` (a demo server checks it on make verify's frozen day, not today), a day either side."""
    now = datetime.now(UTC)
    moments = [now, *(datetime(d.year, d.month, d.day, 12, tzinfo=UTC) for d in days)]
    start, end = min(moments) - timedelta(days=1), max(moments) + timedelta(days=1)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Runway verify throwaway CA for {name}")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.NameConstraints(permitted_subtrees=[x509.DNSName(name)], excluded_subtrees=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(end)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    del ca_key   # never written: nothing more can be signed by this authority
    made = Made(os.path.join(folder, CA_FILE), os.path.join(folder, "server.pem"), os.path.join(folder, "server.key"))
    _write(made.ca, ca.public_bytes(serialization.Encoding.PEM))
    _write(made.cert, cert.public_bytes(serialization.Encoding.PEM))
    _write(made.key, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return made
