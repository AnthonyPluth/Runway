"""A platform authenticator in software, for the app lock's tests (tests/test_applock.py, tests/test_api_contract.py):
what navigator.credentials.create and .get hand the web app, for Runway's address."""
import base64
import hashlib
import json
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from runway import applock

ORIGIN, RP_ID = "https://runway.example", "runway.example"
OIDC_ENV = {"OIDC_ISSUER": "https://idp.example", "OIDC_CLIENT_ID": "runway", "RUNWAY_PUBLIC_URL": ORIGIN,
            "OIDC_ALLOWED_EMAILS": "me@example.com"}


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Authenticator:
    """A platform authenticator in software: what navigator.credentials.create and .get would hand the web app."""

    def __init__(self, alg=applock.ES256, rp_id=RP_ID, counter=0):
        self.alg, self.rp_id, self.counter = alg, rp_id, counter
        self.key = ec.generate_private_key(ec.SECP256R1()) if alg == applock.ES256 else rsa.generate_private_key(65537, 2048)
        self.cred = secrets.token_bytes(16)

    def spki(self) -> bytes:
        return self.key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    def _auth_data(self, flags: int, attested: bool) -> bytes:
        ad = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + self.counter.to_bytes(4, "big")
        if attested:
            ad += b"\0" * 16 + len(self.cred).to_bytes(2, "big") + self.cred + b"\xa0"   # (a COSE key goes here; unread)
        return ad

    @staticmethod
    def _client(kind, challenge, origin, **extra) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False, **extra}).encode()

    def create(self, challenge: str, origin=ORIGIN, flags=applock.UP | applock.UV | applock.AT, idle=60, **extra) -> dict:
        return {"credential_id": b64(self.cred), "client_data": b64(self._client("webauthn.create", challenge, origin, **extra)),
                "authenticator_data": b64(self._auth_data(flags, True)), "public_key": b64(self.spki()), "alg": self.alg,
                "idle": idle}

    def get(self, challenge: str, origin=ORIGIN, flags=applock.UP | applock.UV, kind="webauthn.get", **extra) -> dict:
        self.counter += 1 if self.counter else 0   # (Apple's passkeys always say 0)
        cd, ad = self._client(kind, challenge, origin, **extra), self._auth_data(flags, False)
        signed = ad + hashlib.sha256(cd).digest()
        sig = (self.key.sign(signed, ec.ECDSA(hashes.SHA256())) if self.alg == applock.ES256
               else self.key.sign(signed, padding.PKCS1v15(), hashes.SHA256()))
        return {"credential_id": b64(self.cred), "client_data": b64(cd), "authenticator_data": b64(ad), "signature": b64(sig)}
