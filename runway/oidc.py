"""Sign-in with an OpenID Connect provider (Authentik, Authelia, Keycloak, Pocket ID, Google, Microsoft, ...).

Authorization code flow with PKCE. The ID token is checked for issuer, audience, expiry and nonce, and its RS256
signature is verified against the provider's published keys. (For other signing algorithms Runway relies on having
received the token straight from the provider's token endpoint over HTTPS, which the OIDC spec allows.)
Only people on the allow-list get in; sessions are random tokens kept (hashed) in the database.

Configuration (environment variables):
  OIDC_ISSUER          e.g. https://auth.example.com/application/o/runway/
  OIDC_CLIENT_ID
  OIDC_CLIENT_SECRET   leave empty for a public client (PKCE only)
  RUNWAY_PUBLIC_URL    the address you open Runway at, e.g. https://runway.example.com; the provider must allow
                       <RUNWAY_PUBLIC_URL>/auth/callback as a redirect URI
  OIDC_ALLOWED_EMAILS  comma-separated; and/or
  OIDC_ALLOWED_GROUPS  comma-separated group names from the "groups" claim
  OIDC_ALLOW_ANY_USER  1 to let anyone the provider signs in use Runway (only for a provider you fully control)
  OIDC_SCOPES          default "openid email profile" (add "groups" if your provider needs it for the claim)
  RUNWAY_SESSION_DAYS  default 14
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db

LOGIN_TTL = 600            # seconds to finish signing in at the provider
_discovery: dict = {}
_jwks: dict = {}


class OIDCError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ configuration

def config() -> dict:
    e = os.environ.get
    split = lambda v: {x.strip().lower() for x in (v or "").split(",") if x.strip()}
    public = (e("RUNWAY_PUBLIC_URL") or "").rstrip("/")
    return {
        "issuer": (e("OIDC_ISSUER") or "").strip(),
        "client_id": (e("OIDC_CLIENT_ID") or "").strip(),
        "client_secret": e("OIDC_CLIENT_SECRET") or "",
        "public_url": public,
        "redirect_uri": f"{public}/auth/callback" if public else "",
        "scopes": e("OIDC_SCOPES") or "openid email profile",
        "emails": split(e("OIDC_ALLOWED_EMAILS")),
        "groups": split(e("OIDC_ALLOWED_GROUPS")),
        "any_user": e("OIDC_ALLOW_ANY_USER") == "1",
        "session_days": int(e("RUNWAY_SESSION_DAYS") or 14),
        "secure_cookie": public.startswith("https://"),
    }


def enabled() -> bool:
    return bool(os.environ.get("OIDC_ISSUER"))


def check_config() -> list[str]:
    """Problems that should stop Runway from starting."""
    c = config()
    problems = []
    for key, env in (("issuer", "OIDC_ISSUER"), ("client_id", "OIDC_CLIENT_ID"), ("public_url", "RUNWAY_PUBLIC_URL")):
        if not c[key]:
            problems.append(f"{env} is not set")
    if c["public_url"] and not c["public_url"].startswith(("http://", "https://")):
        problems.append("RUNWAY_PUBLIC_URL must start with http:// or https://")
    if not (c["emails"] or c["groups"] or c["any_user"]):
        problems.append("set OIDC_ALLOWED_EMAILS and/or OIDC_ALLOWED_GROUPS (or OIDC_ALLOW_ANY_USER=1) so only you get in")
    return problems


# ------------------------------------------------------------------------------------------------ HTTP helpers

def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        import certifi  # type: ignore

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Runway/0.1", **(headers or {})})
    with urllib.request.urlopen(req, timeout=15, context=_ctx()) as r:
        return json.loads(r.read().decode())


def discovery() -> dict:
    c = config()
    cached = _discovery.get(c["issuer"])
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    url = c["issuer"].rstrip("/") + "/.well-known/openid-configuration"
    try:
        d = _get_json(url)
    except (urllib.error.URLError, ValueError, OSError) as e:
        raise OIDCError(f"Couldn't read the provider's settings at {url}: {e}") from e
    if d.get("issuer", "").rstrip("/") != c["issuer"].rstrip("/"):
        raise OIDCError(f"The provider says its issuer is {d.get('issuer')!r}, not {c['issuer']!r}. Check OIDC_ISSUER.")
    _discovery[c["issuer"]] = (time.time(), d)
    return d


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ------------------------------------------------------------------------------------------------ login flow

def start_login(conn, next_path: str = "/") -> tuple[str, str]:
    """Returns (provider URL to send the browser to, value for the short-lived login cookie)."""
    c, d = config(), discovery()
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = _b64e(hashlib.sha256(verifier.encode()).digest())
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"
    conn.execute("DELETE FROM auth_pending WHERE created < ?", (time.time() - LOGIN_TTL,))
    conn.execute("INSERT INTO auth_pending(state, nonce, verifier, next, created) VALUES (?,?,?,?,?)",
                 (state, nonce, verifier, next_path, time.time()))
    params = {"response_type": "code", "client_id": c["client_id"], "redirect_uri": c["redirect_uri"],
              "scope": c["scopes"], "state": state, "nonce": nonce,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    return d["authorization_endpoint"] + ("&" if "?" in d["authorization_endpoint"] else "?") + urllib.parse.urlencode(params), state


def finish_login(conn, params: dict, login_cookie: str | None) -> tuple[str, str]:
    """Handle the provider's redirect back. Returns (session token for the cookie, path to go to)."""
    if params.get("error"):
        raise OIDCError(f"The provider said: {params.get('error_description') or params['error']}")
    state, code = params.get("state") or "", params.get("code") or ""
    if not state or not code:
        raise OIDCError("The sign-in response was missing its code. Please try again.")
    if not login_cookie or not secrets.compare_digest(login_cookie, state):
        raise OIDCError("This sign-in didn't start in this browser (or took too long). Please try again.")
    row = conn.execute("SELECT * FROM auth_pending WHERE state=?", (state,)).fetchone()
    conn.execute("DELETE FROM auth_pending WHERE state=?", (state,))
    if not row or time.time() - row["created"] > LOGIN_TTL:
        raise OIDCError("That sign-in link has expired. Please try again.")
    conn.commit()   # nothing held while we talk to the provider

    c, d = config(), discovery()
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": c["redirect_uri"], "code_verifier": row["verifier"]}
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": "Runway/0.1"}
    methods = d.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if c["client_secret"] and "client_secret_basic" in methods:
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{urllib.parse.quote(c['client_id'], safe='')}:{urllib.parse.quote(c['client_secret'], safe='')}".encode()).decode()
    else:
        form["client_id"] = c["client_id"]
        if c["client_secret"]:
            form["client_secret"] = c["client_secret"]
    req = urllib.request.Request(d["token_endpoint"], data=urllib.parse.urlencode(form).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ctx()) as r:
            tokens = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        raise OIDCError(f"The provider refused the sign-in ({e.code}: {detail}). Check the client ID, secret and redirect URI.") from e
    except (urllib.error.URLError, OSError) as e:
        raise OIDCError(f"Couldn't reach the provider: {e}") from e

    claims = verify_id_token(tokens.get("id_token") or "", row["nonce"], d)
    info = dict(claims)
    if tokens.get("access_token") and d.get("userinfo_endpoint") and not (claims.get("email") and "groups" in claims):
        try:   # some providers only put email / groups in userinfo
            ui = _get_json(d["userinfo_endpoint"], {"Authorization": f"Bearer {tokens['access_token']}"})
            if ui.get("sub") == claims.get("sub"):
                info = {**ui, **claims, "groups": claims.get("groups", ui.get("groups"))}
        except (urllib.error.URLError, ValueError, OSError):
            pass
    who = authorize(info)
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute("INSERT INTO auth_sessions(token_hash, sub, email, name, created, expires, id_token) VALUES (?,?,?,?,?,?,?)",
                 (_hash(token), who["sub"], who["email"], who["name"], now, now + c["session_days"] * 86400,
                  tokens.get("id_token")))
    remember_user(conn, who["sub"], who["email"], who["name"], info.get("given_name"), now)
    return token, row["next"] or "/"


def first_name(name: str | None, email: str | None, given: str | None = None) -> str:
    if given and given.strip():
        return given.strip().split()[0]
    if name and name.strip() and "@" not in name:
        return name.strip().split()[0]
    local = (email or "").split("@")[0]
    return (local.split(".")[0].split("_")[0] or "Someone").capitalize()


def remember_user(conn, sub, email, name, given=None, when=None) -> None:
    """Keep a list of people who've signed in, so accounts can be assigned to them."""
    if not sub:
        return
    conn.execute("INSERT INTO users(sub, email, name, first_name, last_seen) VALUES (?,?,?,?,?) ON CONFLICT(sub) DO UPDATE SET "
                 "email=excluded.email, name=excluded.name, first_name=excluded.first_name, last_seen=excluded.last_seen",
                 (sub, email, name, first_name(name, email, given), when or time.time()))


def backfill_users(conn) -> None:
    """People signed in before the users list existed."""
    for r in conn.execute("SELECT sub, email, name, MAX(created) AS t FROM auth_sessions WHERE sub IS NOT NULL GROUP BY sub, email, name").fetchall():
        if not conn.execute("SELECT 1 FROM users WHERE sub=?", (r["sub"],)).fetchone():
            remember_user(conn, r["sub"], r["email"], r["name"], None, r["t"])


def authorize(info: dict) -> dict:
    c = config()
    email = (info.get("email") or "").strip().lower()
    groups = info.get("groups") or []
    if isinstance(groups, str):
        groups = [groups]
    groups = {str(g).strip().lower() for g in groups}
    if info.get("email_verified") is False and email in c["emails"]:
        raise OIDCError(f"The provider hasn't verified {email}, so Runway can't let it in.")
    ok = c["any_user"] or (email and email in c["emails"]) or bool(groups & c["groups"])
    if not ok:
        raise OIDCError(f"{email or info.get('sub')} isn't on Runway's allow-list (OIDC_ALLOWED_EMAILS / OIDC_ALLOWED_GROUPS).")
    return {"sub": str(info.get("sub")), "email": email or None,
            "name": info.get("name") or info.get("preferred_username") or email or str(info.get("sub"))}


# ------------------------------------------------------------------------------------------------ ID token

def verify_id_token(id_token: str, nonce: str, d: dict, now: float | None = None) -> dict:
    c = config()
    now = now or time.time()
    parts = id_token.split(".")
    if len(parts) != 3:
        raise OIDCError("The provider didn't return an ID token.")
    try:
        header, claims = json.loads(_b64d(parts[0])), json.loads(_b64d(parts[1]))
    except ValueError as e:
        raise OIDCError("The ID token couldn't be read.") from e
    alg = header.get("alg")
    if alg == "RS256":
        if not _verify_rs256(f"{parts[0]}.{parts[1]}".encode(), _b64d(parts[2]), header.get("kid"), d):
            raise OIDCError("The ID token's signature didn't check out.")
    elif alg in (None, "none") or alg.startswith("HS") and not c["client_secret"]:
        raise OIDCError(f"Unsupported ID token signature ({alg}).")
    elif alg.startswith("HS"):
        import hmac
        digest = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}.get(alg)
        if not digest or not hmac.compare_digest(hmac.new(c["client_secret"].encode(), f"{parts[0]}.{parts[1]}".encode(), digest).digest(), _b64d(parts[2])):
            raise OIDCError("The ID token's signature didn't check out.")
    elif not d["token_endpoint"].startswith("https://"):
        # Other algorithms (e.g. ES256): the spec lets us trust a token that came straight from the token
        # endpoint over TLS. Over plain http we can't, so refuse.
        raise OIDCError(f"ID tokens signed with {alg} need the provider to use https. Switch the provider's signing key to RS256 or use https.")
    if claims.get("iss", "").rstrip("/") != c["issuer"].rstrip("/"):
        raise OIDCError("The ID token is from a different issuer.")
    aud = claims.get("aud")
    if c["client_id"] not in (aud if isinstance(aud, list) else [aud]):
        raise OIDCError("The ID token is meant for a different app.")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") not in (None, c["client_id"]):
        raise OIDCError("The ID token is meant for a different app.")
    if float(claims.get("exp", 0)) < now - 60:
        raise OIDCError("The ID token has expired. Check this machine's clock.")
    if float(claims.get("iat", now)) > now + 300:
        raise OIDCError("The ID token is dated in the future. Check this machine's clock.")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("The ID token doesn't belong to this sign-in.")
    if not claims.get("sub"):
        raise OIDCError("The ID token has no subject.")
    return claims


_SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _verify_rs256(signed: bytes, sig: bytes, kid: str | None, d: dict, refreshed: bool = False) -> bool:
    uri = d.get("jwks_uri")
    if not uri:
        return False
    if uri not in _jwks or refreshed:
        _jwks[uri] = _get_json(uri).get("keys", [])
    keys = [k for k in _jwks[uri] if k.get("kty") == "RSA" and k.get("use", "sig") == "sig" and (not kid or k.get("kid") == kid)]
    if not keys and not refreshed:        # keys rotated since we cached them
        return _verify_rs256(signed, sig, kid, d, refreshed=True)
    digest = hashlib.sha256(signed).digest()
    for k in keys:
        n, e = int.from_bytes(_b64d(k["n"]), "big"), int.from_bytes(_b64d(k["e"]), "big")
        size = (n.bit_length() + 7) // 8
        if len(sig) != size or size < 256:   # require 2048-bit keys or bigger
            continue
        em = pow(int.from_bytes(sig, "big"), e, n).to_bytes(size, "big")
        expected = b"\x00\x01" + b"\xff" * (size - 3 - len(_SHA256_PREFIX) - 32) + b"\x00" + _SHA256_PREFIX + digest
        if secrets.compare_digest(em, expected):
            return True
    return False


# ------------------------------------------------------------------------------------------------ sessions

def session_user(conn, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute("SELECT * FROM auth_sessions WHERE token_hash=?", (_hash(token),)).fetchone()
    if not row:
        return None
    if row["expires"] < time.time():
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (row["token_hash"],))
        return None
    return {"sub": row["sub"], "email": row["email"], "name": row["name"]}


def logout(conn, token: str | None) -> str:
    """End the session; returns where to send the browser (the provider's sign-out page if it has one)."""
    id_token = None
    if token:
        row = conn.execute("SELECT id_token FROM auth_sessions WHERE token_hash=?", (_hash(token),)).fetchone()
        id_token = row["id_token"] if row else None
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_hash(token),))
    conn.execute("DELETE FROM auth_sessions WHERE expires < ?", (time.time(),))
    c = config()
    try:
        end = discovery().get("end_session_endpoint")
    except OIDCError:
        end = None
    if end:
        q = {"client_id": c["client_id"], "post_logout_redirect_uri": c["public_url"] + "/auth/signed-out"}
        if id_token:
            q["id_token_hint"] = id_token
        return end + ("&" if "?" in end else "?") + urllib.parse.urlencode(q)
    return "/auth/signed-out"
