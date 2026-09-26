"""Sign-in with an OpenID Connect provider (Authentik, Authelia, Keycloak, Pocket ID, Google, Microsoft, ...).

Authorization code flow with PKCE. The ID token is verified with PyJWT: its signature against the provider's published
keys (RSA, RSA-PSS, EC or EdDSA; or the client secret for HS256), plus issuer, audience, expiry and this sign-in's nonce.
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

import jwt

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

ASYMMETRIC = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
SYMMETRIC = {"HS256", "HS384", "HS512"}


def _signing_key(kid: str | None, alg: str, d: dict):
    """The provider's public key for this token, from its JWKS (refetched once if the key isn't there: rotation)."""
    uri = d.get("jwks_uri")
    if not uri:
        raise OIDCError("The provider doesn't publish its signing keys (no jwks_uri).")
    for refresh in (False, True):
        if uri not in _jwks or refresh:
            try:
                _jwks[uri] = jwt.PyJWKSet.from_dict(_get_json(uri))
            except (jwt.PyJWKSetError, urllib.error.URLError, ValueError, OSError) as e:
                raise OIDCError(f"Couldn't read the provider's signing keys: {e}") from e
        keys = [k for k in _jwks[uri].keys if (not kid or k.key_id == kid) and k.public_key_use in (None, "sig")]
        if keys:
            return keys[0].key
    raise OIDCError("The ID token was signed with a key the provider doesn't publish.")


def verify_id_token(id_token: str, nonce: str, d: dict) -> dict:
    """Check an ID token the way OIDC requires: signature, issuer, audience, expiry, and this sign-in's nonce."""
    c = config()
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.DecodeError as e:
        raise OIDCError("The provider didn't return a readable ID token.") from e
    alg = header.get("alg")
    if alg in ASYMMETRIC:
        key = _signing_key(header.get("kid"), alg, d)
    elif alg in SYMMETRIC and c["client_secret"]:
        key = c["client_secret"]
    else:
        raise OIDCError(f"Unsupported ID token signature ({alg}).")
    try:
        claims = jwt.decode(id_token, key, algorithms=[alg], audience=c["client_id"], leeway=60,
                            options={"require": ["iss", "sub", "aud", "exp", "iat"], "verify_iss": False})
    except jwt.ExpiredSignatureError as e:
        raise OIDCError("The ID token has expired. Check this machine's clock.") from e
    except jwt.ImmatureSignatureError as e:
        raise OIDCError("The ID token is dated in the future. Check this machine's clock.") from e
    except (jwt.InvalidAudienceError, jwt.MissingRequiredClaimError) as e:
        raise OIDCError(f"The ID token is meant for a different app, or is missing details ({e}).") from e
    except jwt.InvalidSignatureError as e:
        raise OIDCError("The ID token's signature didn't check out.") from e
    except jwt.InvalidTokenError as e:
        raise OIDCError(f"The ID token isn't valid: {e}") from e
    # Issuers are compared without a trailing slash, since providers differ on it.
    if str(claims.get("iss", "")).rstrip("/") != c["issuer"].rstrip("/"):
        raise OIDCError("The ID token is from a different issuer.")
    aud = claims.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") not in (None, c["client_id"]):
        raise OIDCError("The ID token is meant for a different app.")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("The ID token doesn't belong to this sign-in.")
    return claims


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
