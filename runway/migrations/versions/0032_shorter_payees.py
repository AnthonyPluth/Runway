"""Shorter merchant names for transactions synced before them: the bank's ACH and bill-pay words cut off the payee
("Target Cach Tran Cash" -> "Target", runway/payees.py), as a sync now does. The bank's text stays in the description.

A name you gave a transaction stays: nothing records a rename, so only a payee that's the bank's own text is shortened
(every word of it in the description, payees.from_bank), never one a rule renames to, nor a merchant Plaid named.
What was made from a long name follows it: a recurring item matching it also matches the shorter one (and is called
by it, if it was called by the long one), a logo you chose for it is kept for the shorter one, and so is a recurring
suggestion you dismissed. Rules made from a long name match the shorter one on their own (rules._text_matches).

Revision ID: 0032
Revises: 0031
"""
import json
import re

import sqlalchemy as sa
from alembic import op

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


# runway/payees.py as it was when this migration was written, frozen here so 0032 always does the same thing (later
# changes to the shortening apply to new syncs, not to this one-time pass).
_LEAD = re.compile(r"^(?:direct deposit|direct dep|dir dep)\s+", re.I)
_MARKER = re.compile(r"(?:c|debit|credit)?ach|ppd|ccd|baweb|cpurchase")
_TAIL_WORDS = {"cash", "tran", "trans", "transfer", "xfer", "pay", "pmt", "pymt", "payment", "debit", "credit", "web",
               "purchase", "deposit", "dep", "withdrawal", "wd", "bill", "online", "id"}
_PAY_WORDS = {"pay", "pmt", "pymt", "payment"}


def _plain(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _bank_tail(words: list[str]) -> bool:
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if _MARKER.fullmatch(w) or (w == "web" and nxt in _PAY_WORDS) or (len(w) == 1 and w.isalpha() and nxt in _TAIL_WORDS):
            return True
    return False


def _noise(w: str) -> bool:
    return bool(_MARKER.fullmatch(w)) or w in _TAIL_WORDS or (len(w) == 1 and w.isalpha())


def shorten(name: str | None) -> str:
    s = " ".join((name or "").split())
    words = _LEAD.sub("", s).split(" ")
    plain = [_plain(w) for w in words]
    cut = len(words)
    while cut > 1 and _noise(plain[cut - 1]):
        cut -= 1
    if cut == len(words) or not _bank_tail(plain[cut:]):
        return s
    return " ".join(words[:cut])


def from_bank(payee: str | None, description: str | None) -> bool:
    desc = (description or "").lower()
    return bool(desc) and all(_plain(w) in desc for w in (payee or "").split())


def _key(s: str) -> str:
    return " ".join((s or "").lower().split())


def upgrade() -> None:
    bind = op.get_bind()
    renames = {_key(r) for (r,) in bind.execute(sa.text("SELECT rename FROM rules WHERE rename IS NOT NULL"))}
    names: dict[str, str] = {}   # the long payee (as a key) -> its shorter name
    for payee, desc in bind.execute(sa.text(
            "SELECT DISTINCT payee, description FROM transactions WHERE payee IS NOT NULL AND merchant_id IS NULL")).fetchall():
        new = shorten(payee)
        if new == payee or _key(payee) in renames or not from_bank(payee, desc):
            continue
        bind.execute(sa.text("UPDATE transactions SET payee = :new WHERE payee = :old AND description = :desc AND merchant_id IS NULL"),
                     {"new": new, "old": payee, "desc": desc})
        names[_key(payee)] = new
    if not names:
        return
    for rid, name, match in bind.execute(sa.text('SELECT id, name, "match" FROM recurring')).fetchall():
        texts = [m for m in dict.fromkeys(_key(line) for line in (match or name or "").splitlines()) if m]
        more = [names[m].lower() for m in texts if m in names and names[m].lower() not in texts]
        renamed = names.get(_key(name)) if name else None
        if more or renamed:
            bind.execute(sa.text('UPDATE recurring SET "match" = :match, name = :name WHERE id = :id'),
                         {"match": "\n".join(dict.fromkeys(texts + more)) or None, "name": renamed or name, "id": rid})
    chosen = {k: (w, h) for k, w, h in bind.execute(sa.text("SELECT key, website, hidden FROM merchant_logos")).fetchall()}
    for old, new in names.items():
        if old in chosen and _key(new) not in chosen:
            bind.execute(sa.text("INSERT INTO merchant_logos(key, website, hidden) VALUES (:key, :website, :hidden)"),
                         {"key": _key(new), "website": chosen[old][0], "hidden": chosen[old][1]})
            chosen[_key(new)] = chosen[old]
    row = bind.execute(sa.text("SELECT value FROM settings WHERE key = 'recurring_suggestions_dismissed'")).fetchone()
    try:
        dismissed = json.loads(row[0] or "[]") if row else []
    except ValueError:
        return
    if not isinstance(dismissed, list):
        return
    more = []
    for k in dismissed:   # forecast.suggestion_key: account|payee|frequency
        parts = k.split("|") if isinstance(k, str) else []
        if len(parts) >= 3 and parts[-2] in names:
            more.append("|".join([*parts[:-2], names[parts[-2]].lower(), parts[-1]]))
    if more:
        bind.execute(sa.text("UPDATE settings SET value = :value WHERE key = 'recurring_suggestions_dismissed'"),
                     {"value": json.dumps(sorted({k for k in dismissed if isinstance(k, str)} | set(more)))})


def downgrade() -> None:
    pass   # the long names are still in each transaction's description
