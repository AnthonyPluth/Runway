"""Shorter merchant names: the ACH and bill-pay words a bank puts after the merchant cut off ("Target Cach Tran Cash"
-> "Target", "Fifth Third Baweb Pay Cash" -> "Fifth Third"), for payees at sync time (categorize.clean_payee); migration
0032 gave transactions synced before the shorter names too.

Careful on purpose: a wrong name is worse than a long one. A tail of transfer words is cut only when it has a word in
it that's never part of a merchant's name (an ACH code, often run together with the word before it where the bank
cuts its fixed-width fields: "DEBIT C" + "ACH TRAN" -> "CACH"), so "Apple Pay", "Apple Cash" and "Charlotte's Web"
stay as they are. The bank's full text stays in the transaction's description.
"""
from __future__ import annotations

import functools
import re

_LEAD = re.compile(r"^(?:direct deposit|direct dep|dir dep)\s+", re.I)
# Never part of a merchant's name: these mark a tail as the bank's.
_MARKER = re.compile(r"(?:c|debit|credit)?ach|ppd|ccd|baweb|cpurchase")
# Words a bank's tail is made of, but that a merchant's name can end with too ("Apple Pay", "Apple Cash").
_TAIL_WORDS = {"cash", "tran", "trans", "transfer", "xfer", "pay", "pmt", "pymt", "payment", "debit", "credit", "web",
               "purchase", "deposit", "dep", "withdrawal", "wd", "bill", "online", "id"}
_PAY_WORDS = {"pay", "pmt", "pymt", "payment"}


def _plain(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _bank_tail(words: list[str]) -> bool:
    """Whether these last words of a payee are the bank's: all transfer words, with one that only a bank writes
    (an ACH code, "web pay", or a letter cut off a fixed-width field before another transfer word: "Target C Cash")."""
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if _MARKER.fullmatch(w) or (w == "web" and nxt in _PAY_WORDS) or (len(w) == 1 and w.isalpha() and nxt in _TAIL_WORDS):
            return True
    return False


def _noise(w: str) -> bool:
    return bool(_MARKER.fullmatch(w)) or w in _TAIL_WORDS or (len(w) == 1 and w.isalpha())


def shorten(name: str | None) -> str:
    """The payee without the bank's transfer words after the merchant (and "Direct Deposit" before it), when they're
    clearly the bank's; otherwise as it was."""
    s = " ".join((name or "").split())
    rest = _LEAD.sub("", s)
    words = rest.split(" ")
    plain = [_plain(w) for w in words]
    cut = len(words)
    while cut > 1 and _noise(plain[cut - 1]):
        cut -= 1
    if cut == len(words) or not _bank_tail(plain[cut:]):
        return s
    return " ".join(words[:cut])


def from_bank(payee: str | None, description: str | None) -> bool:
    """Whether a payee is (a tidied piece of) the bank's own text, rather than a name you gave it: nothing records a
    rename, but a name you typed rarely has every word in the bank's description."""
    desc = (description or "").lower()
    return bool(desc) and all(_plain(w) in desc for w in (payee or "").split())


@functools.lru_cache(maxsize=4096)
def short_match(text: str) -> str | None:
    """For a rule's text made from a long payee ("target cach tran cash"): the shorter name that payee has now
    ("target"), so the rule still matches transactions synced since. None if it's no shorter."""
    t = " ".join((text or "").lower().split())
    short = shorten(t).lower()
    return short if short != t and len(short) >= 3 else None
