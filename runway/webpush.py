"""Web Push (RFC 8030) with VAPID (RFC 8292) and encrypted payloads (RFC 8291), in plain Python.

Python's standard library has no elliptic curves or AES, so the few pieces Web Push needs are here: P-256 ECDH and
ECDSA, AES-128-GCM, and HKDF. They're checked against published test vectors in tests/test_webpush.py. None of this is
used for anything but sending notifications to your own devices.

Works with every browser push service, including Apple's (iOS 16.4+, for web apps added to the Home Screen).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request

# ------------------------------------------------------------------------------------------------ P-256

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _inv(x: int, m: int) -> int:
    return pow(x, -1, m)


# Jacobian coordinates (X, Y, Z) represent the affine point (X/Z^2, Y/Z^3); Z = 0 is the point at infinity.
def _double(p1):
    X1, Y1, Z1 = p1
    if Z1 == 0 or Y1 == 0:
        return (0, 1, 0)
    YY = Y1 * Y1 % P
    S = 4 * X1 * YY % P
    ZZ = Z1 * Z1 % P
    M = 3 * (X1 - ZZ) * (X1 + ZZ) % P          # a = -3
    X3 = (M * M - 2 * S) % P
    Y3 = (M * (S - X3) - 8 * YY * YY) % P
    Z3 = 2 * Y1 * Z1 % P
    return (X3, Y3, Z3)


def _add(p1, p2):
    X1, Y1, Z1 = p1
    X2, Y2, Z2 = p2
    if Z1 == 0:
        return p2
    if Z2 == 0:
        return p1
    Z1Z1, Z2Z2 = Z1 * Z1 % P, Z2 * Z2 % P
    U1, U2 = X1 * Z2Z2 % P, X2 * Z1Z1 % P
    S1, S2 = Y1 * Z2 * Z2Z2 % P, Y2 * Z1 * Z1Z1 % P
    if U1 == U2:
        return _double(p1) if S1 == S2 else (0, 1, 0)
    H, R = (U2 - U1) % P, (S2 - S1) % P
    HH = H * H % P
    HHH = H * HH % P
    V = U1 * HH % P
    X3 = (R * R - HHH - 2 * V) % P
    Y3 = (R * (V - X3) - S1 * HHH) % P
    Z3 = H * Z1 * Z2 % P
    return (X3, Y3, Z3)


def _mul(k: int, point: tuple[int, int]) -> tuple[int, int]:
    """k * point, in affine coordinates."""
    if k % N == 0:
        raise ValueError("point at infinity")
    result, addend = (0, 1, 0), (point[0], point[1], 1)
    while k:
        if k & 1:
            result = _add(result, addend)
        addend = _double(addend)
        k >>= 1
    X, Y, Z = result
    zi = _inv(Z, P)
    return (X * zi * zi % P, Y * zi * zi * zi % P)


def _on_curve(pt: tuple[int, int]) -> bool:
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def encode_point(pt: tuple[int, int]) -> bytes:
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


def decode_point(raw: bytes) -> tuple[int, int]:
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("expected an uncompressed P-256 public key")
    pt = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big"))
    if not _on_curve(pt):
        raise ValueError("public key is not on the curve")
    return pt


def new_private_key() -> int:
    return secrets.randbelow(N - 1) + 1


def public_key(private: int) -> bytes:
    return encode_point(_mul(private, G))


def ecdh(private: int, peer_public: bytes) -> bytes:
    return _mul(private, decode_point(peer_public))[0].to_bytes(32, "big")


def _bits2int(h: bytes) -> int:
    return int.from_bytes(h, "big") % N


def ecdsa_sign(private: int, message: bytes) -> bytes:
    """ES256 signature (r || s, 64 bytes) with a deterministic nonce (RFC 6979)."""
    h = hashlib.sha256(message).digest()
    z = _bits2int(h)
    x = private.to_bytes(32, "big")
    V, K = b"\x01" * 32, b"\x00" * 32
    h1 = (int.from_bytes(h, "big") % N).to_bytes(32, "big")
    K = hmac.new(K, V + b"\x00" + x + h1, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    K = hmac.new(K, V + b"\x01" + x + h1, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    while True:
        V = hmac.new(K, V, hashlib.sha256).digest()
        k = int.from_bytes(V, "big")
        if 1 <= k < N:
            r = _mul(k, G)[0] % N
            s = _inv(k, N) * (z + r * private) % N
            if r and s:
                return r.to_bytes(32, "big") + s.to_bytes(32, "big")
        K = hmac.new(K, V + b"\x00", hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()


def ecdsa_verify(public: bytes, message: bytes, sig: bytes) -> bool:
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if not (1 <= r < N and 1 <= s < N):
        return False
    Q = decode_point(public)
    w = _inv(s, N)
    z = _bits2int(hashlib.sha256(message).digest())
    X, Y, Z = _add(_to_jac(_mul(z * w % N, G)), _to_jac(_mul(r * w % N, Q)))
    if Z == 0:
        return False
    zi = _inv(Z, P)
    return X * zi * zi % P % N == r


def _to_jac(pt):
    return (pt[0], pt[1], 1)


# ------------------------------------------------------------------------------------------------ AES-128-GCM

def _gf_mul(a: int, b: int) -> int:
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = ((a << 1) ^ 0x11B) if a & 0x80 else a << 1
        b >>= 1
    return out


def _sbox() -> list[int]:
    """AES S-box from its definition: the inverse in GF(2^8), then the affine transform."""
    box = []
    for x in range(256):
        inv = 0
        if x:
            inv = 1
            for _ in range(254):          # x^254 = x^-1
                inv = _gf_mul(inv, x)
        rot = lambda v, n: ((v << n) | (v >> (8 - n))) & 0xFF
        box.append(inv ^ rot(inv, 1) ^ rot(inv, 2) ^ rot(inv, 3) ^ rot(inv, 4) ^ 0x63)
    return box


SBOX = _sbox()


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expand_key(key: bytes) -> list[list[int]]:
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]


def _encrypt_block(round_keys: list[list[int]], block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, round_keys[0])]
    for rnd in range(1, 11):
        s = [SBOX[b] for b in s]
        # ShiftRows (state is column-major: index = col*4 + row)
        s = [s[((c + r) % 4) * 4 + r] for c in range(4) for r in range(4)]
        if rnd != 10:
            out = []
            for c in range(4):
                a = s[c * 4:c * 4 + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                out += [a[i] ^ t ^ _xtime(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
            s = out
        s = [b ^ k for b, k in zip(s, round_keys[rnd])]
    return bytes(s)


def _gmul(x: int, y: int) -> int:
    R = 0xE1000000000000000000000000000000
    z, v = 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = (v >> 1) ^ R if v & 1 else v >> 1
    return z


def aes128gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Ciphertext followed by the 16-byte tag. iv is 12 bytes."""
    rk = _expand_key(key)
    H = int.from_bytes(_encrypt_block(rk, bytes(16)), "big")
    j0 = iv + b"\x00\x00\x00\x01"
    ct = bytearray()
    counter = 1
    for i in range(0, len(plaintext), 16):
        counter += 1
        ks = _encrypt_block(rk, iv + counter.to_bytes(4, "big"))
        chunk = plaintext[i:i + 16]
        ct += bytes(a ^ b for a, b in zip(chunk, ks))

    def blocks(data: bytes):
        for i in range(0, len(data), 16):
            yield int.from_bytes(data[i:i + 16].ljust(16, b"\x00"), "big")

    g = 0
    for blk in list(blocks(aad)) + list(blocks(bytes(ct))):
        g = _gmul(g ^ blk, H)
    g = _gmul(g ^ ((len(aad) * 8) << 64 | (len(ct) * 8)), H)
    tag = (g ^ int.from_bytes(_encrypt_block(rk, j0), "big")).to_bytes(16, "big")
    return bytes(ct) + tag


def aes128gcm_decrypt(key: bytes, iv: bytes, data: bytes, aad: bytes = b"") -> bytes:
    ct, tag = data[:-16], data[-16:]
    # CTR decryption is the same as encryption; recompute the tag to check it.
    pt = aes128gcm_encrypt(key, iv, ct, aad)[:-16]
    if not hmac.compare_digest(aes128gcm_encrypt(key, iv, pt, aad)[-16:], tag):
        raise ValueError("authentication failed")
    return pt


# ------------------------------------------------------------------------------------------------ RFC 8291

def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encrypt(payload: bytes, ua_public: bytes, auth_secret: bytes, *, as_private: int | None = None,
            salt: bytes | None = None, record_size: int = 4096) -> bytes:
    """The aes128gcm body for one push message (a single record)."""
    as_private = as_private or new_private_key()
    as_public = public_key(as_private)
    salt = salt or secrets.token_bytes(16)
    shared = ecdh(as_private, ua_public)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) + 17 > record_size:
        raise ValueError("payload too large for one record")
    body = aes128gcm_encrypt(cek, nonce, payload + b"\x02")
    return salt + struct.pack("!IB", record_size, len(as_public)) + as_public + body


def decrypt(body: bytes, ua_private: int, auth_secret: bytes) -> bytes:
    """The receiving side (browsers do this); used by the tests."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_public, data = body[21:21 + idlen], body[21 + idlen:]
    ua_public = public_key(ua_private)
    shared = ecdh(ua_private, as_public)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    pt = aes128gcm_decrypt(cek, nonce, data)
    return pt.rstrip(b"\x00")[:-1]   # drop padding and the \x02 delimiter


# ------------------------------------------------------------------------------------------------ VAPID and sending

def vapid_keys(conn) -> tuple[int, str]:
    """This server's VAPID key pair (made once and kept in the database). Returns (private, public as base64url)."""
    from . import db
    raw = db.get_setting(conn, "vapid_private_key")
    if not raw:
        raw = format(new_private_key(), "064x")
        db.set_setting(conn, "vapid_private_key", raw)
    priv = int(raw, 16)
    return priv, b64u(public_key(priv))


def vapid_header(private: int, public_b64: str, endpoint: str, subject: str, now: float | None = None) -> str:
    u = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": subject}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    sig = ecdsa_sign(private, f"{head}.{body}".encode())
    return f"vapid t={head}.{body}.{b64u(sig)}, k={public_b64}"


class Gone(Exception):
    """The subscription no longer exists (the app was removed or notifications were turned off)."""


def send(sub: dict, message: dict, private: int, public_b64: str, subject: str, ttl: int = 86400,
         urgency: str = "normal", timeout: int = 15) -> int:
    """Send one notification. sub = {endpoint, p256dh, auth}. Raises Gone for dead subscriptions."""
    body = encrypt(json.dumps(message, separators=(",", ":")).encode(), unb64u(sub["p256dh"]), unb64u(sub["auth"]))
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "Content-Type": "application/octet-stream", "Content-Encoding": "aes128gcm", "TTL": str(ttl),
        "Urgency": urgency, "Authorization": vapid_header(private, public_b64, sub["endpoint"], subject)})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return r.status
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            raise Gone(sub["endpoint"]) from e
        detail = e.read()[:300].decode(errors="replace")
        raise RuntimeError(f"push service said {e.code}: {detail}") from e
