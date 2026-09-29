"""Encrypting the secrets Runway keeps: bank access (SimpleFIN access URL, Plaid access tokens), API keys and the
push-notification signing key. A copy of the database on its own (a Postgres dump, a stolen disk image of just the
database, a misplaced file) doesn't give those away.

The key comes from RUNWAY_SECRET_KEY (a long random string; best, since it lives apart from the data), or else from
a key file Runway creates next to the database (RUNWAY_DATA/secret.key). Values are stored as "enc:v1:<Fernet token>";
anything without that prefix is plaintext from an earlier version and is encrypted at the next start (see
encrypt_stored). Backups hold the secrets decrypted, so a backup restores on any machine; keep backups private.

Changing keys: set the new RUNWAY_SECRET_KEY and keep the old one in RUNWAY_SECRET_KEY_OLD (or keep secret.key) for
one start; Runway re-encrypts everything with the new key.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import os
import threading

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from . import settings_keys

PREFIX = "enc:v1:"
KEY_FILE = "secret.key"
MIN_KEY_LENGTH = 32

# settings rows that hold secrets (the rest of the settings table is ordinary preferences)
SECRET_SETTINGS = settings_keys.SECRETS

_lock = threading.Lock()
_cache: dict[tuple, MultiFernet] = {}


class SecretError(Exception):
    """A stored secret can't be decrypted with the keys Runway has (the key changed or was lost)."""


@functools.lru_cache(maxsize=8)
def _from_passphrase(text: str) -> bytes:
    # RUNWAY_SECRET_KEY should be a long random string, but may be a passphrase: scrypt makes guessing it slow.
    return base64.urlsafe_b64encode(hashlib.scrypt(text.encode(), salt=b"runway-secretbox", n=2 ** 15, r=8, p=1,
                                                   maxmem=64 * 1024 * 1024, dklen=32))


def _from_passphrase_v1(text: str) -> bytes:
    """How earlier versions made the key (one SHA-256), still read so secrets saved then are re-encrypted at start."""
    return base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest())


def _data_dir() -> str:
    from . import db
    return db.data_dir()


def key_file_path() -> str:
    return os.path.join(_data_dir(), KEY_FILE)


def _read_or_make_key_file(create: bool) -> bytes | None:
    path = key_file_path()
    try:
        with open(path, "rb") as f:
            return f.read().strip()
    except FileNotFoundError:
        if not create:
            return None
    key = Fernet.generate_key()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:   # another process made it first
        with open(path, "rb") as f:
            return f.read().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(key + b"\n")
    return key


def check_config() -> list[str]:
    """Problems that should stop Runway from starting."""
    k = os.environ.get("RUNWAY_SECRET_KEY") or ""
    if k and len(k) < MIN_KEY_LENGTH:
        return [f"RUNWAY_SECRET_KEY must be at least {MIN_KEY_LENGTH} characters (try: openssl rand -base64 32)"]
    return []


def _box() -> MultiFernet:
    env, old = os.environ.get("RUNWAY_SECRET_KEY") or "", os.environ.get("RUNWAY_SECRET_KEY_OLD") or ""
    ident = (env, old, _data_dir())
    with _lock:
        if ident not in _cache:
            keys = []
            if env:
                keys += [Fernet(_from_passphrase(env)), Fernet(_from_passphrase_v1(env))]
                if old:
                    keys += [Fernet(_from_passphrase(old)), Fernet(_from_passphrase_v1(old))]
                file_key = _read_or_make_key_file(create=False)   # secrets encrypted before the env key was set
            else:
                file_key = _read_or_make_key_file(create=True)
            if file_key:
                keys.append(Fernet(file_key))
            _cache[ident] = MultiFernet(keys)
        return _cache[ident]


def _primary() -> Fernet:
    """The key new secrets are encrypted with (the first of _box's keys)."""
    env = os.environ.get("RUNWAY_SECRET_KEY") or ""
    return Fernet(_from_passphrase(env) if env else _read_or_make_key_file(create=True))


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: str | None) -> str | None:
    if value is None or value == "" or is_encrypted(value):
        return value
    return PREFIX + _box().encrypt(str(value).encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not is_encrypted(value):
        return value
    try:
        return _box().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise SecretError("Runway can't unlock a saved secret with its current key (was RUNWAY_SECRET_KEY changed, or "
                          f"{KEY_FILE} lost?). Put the old key back, or enter that key or connection again.") from e


def reencrypt(value: str | None) -> str | None:
    """Plaintext → encrypted; encrypted with an older key → encrypted with the current one."""
    if not is_encrypted(value):
        return encrypt(value)
    token = value[len(PREFIX):].encode()
    try:
        _primary().decrypt(token)
        return value   # already under the current key
    except InvalidToken:
        return PREFIX + _box().rotate(token).decode()


def encrypt_stored(conn) -> int:
    """Encrypt (or re-encrypt with the current key) every stored secret. Runs at start-up; returns how many changed."""
    changed = 0
    keys = sorted(SECRET_SETTINGS)
    marks = ",".join("?" * len(keys))
    for r in conn.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", keys).fetchall():
        try:
            new = reencrypt(r["value"])
        except InvalidToken:
            print(f"Warning: the saved {r['key']} can't be decrypted with the current key; enter it again in Settings.", flush=True)
            continue
        if new != r["value"]:
            conn.execute("UPDATE settings SET value=? WHERE key=?", (new, r["key"]))
            changed += 1
    for r in conn.execute("SELECT item_id, access_token FROM plaid_items").fetchall():
        try:
            new = reencrypt(r["access_token"])
        except InvalidToken:
            print("Warning: a Plaid connection's access can't be decrypted with the current key; reconnect it.", flush=True)
            continue
        if new != r["access_token"]:
            conn.execute("UPDATE plaid_items SET access_token=? WHERE item_id=?", (new, r["item_id"]))
            changed += 1
    return changed
