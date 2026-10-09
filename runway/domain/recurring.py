"""Recurring items: matching them to real transactions, and working out what the next one will be."""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from sqlalchemy import and_, false, func, insert, or_, select, true, update

from . import bankdays, brands
from .. import dates
from ..storage import db
from ..storage.models import Account, Override, Recurring, RecurringDismissed, Transaction
from ..money import CENT, is_zero

# How far a real payment can land from its expected date and still count as that occurrence.
MATCH_WINDOW_DAYS = {"weekly": 2, "biweekly": 4, "semimonthly": 4, "monthly": 6, "quarterly": 10,
                     "semiannual": 12, "yearly": 12, "dates": 12, "once": 5}
AMOUNT_MODES = {"fixed", "last", "avg3"}
# The "use $X" hint: a fixed amount's latest RECENT_PAYMENTS occurrences all came to more than DRIFT_SHARE of it, or
# DRIFT_DOLLARS, whichever is more, away from it (the same way), and within STALE_TOLERANCE of each other.
DRIFT_SHARE = 0.05
DRIFT_DOLLARS = 2.0
STALE_TOLERANCE = 0.3
RECENT_PAYMENTS = 3
# Paid within this share of an occurrence's amount, it's done (a paycheck a few dollars off); further short, the rest is
# still expected while its matching window is open (a paycheck that came in two parts). Only for money in, or money out
# paid less than half (a $15 charge near a $139 renewal): a bill that just came in cheaper is done, and so is anything
# whose amount is learned from the payments (it varies by design).
SHORTFALL = 0.1


def match_texts(item: dict) -> list[str]:
    """The texts a transaction's payee or description can have to belong to this item, one per line of its merchant
    text (its name when it has none), lowercased."""
    out: list[str] = []
    for line in (item.get("match") or item.get("name") or "").splitlines():
        if (m := line.strip().lower()) and m not in out:
            out.append(m)
    return out


def clean_texts(value) -> str | None:
    """Merchant texts as they're stored: one per line, lowercased, without blanks or repeats (None for none). Takes the
    text as typed, or a list of texts."""
    lines = value if isinstance(value, list) else str(value or "").splitlines()
    return "\n".join(match_texts({"match": "\n".join(str(x) for x in lines)})) or None


def has_text(texts: list[str]):
    """SQL: the transaction's payee or description has one of these texts (none of them, with no texts)."""
    t = Transaction
    return or_(false(), *(db.instr(func.lower(func.coalesce(col, "")), m) > 0 for m in texts for col in (t.payee, t.description)))


def fits_amount(item: dict, amount: float) -> bool:
    """Whether a payment of `amount` is within the item's amount range (amount_min to amount_max, either may be unset)."""
    lo, hi, a = item.get("amount_min"), item.get("amount_max"), abs(amount)
    return (lo is None or a >= lo - CENT) and (hi is None or a <= hi + CENT)


def amount_fits(item: dict):
    """SQL: the transaction's amount is within the item's amount range (fits_amount)."""
    t, lo, hi = Transaction, item.get("amount_min"), item.get("amount_max")
    return and_(true(), *([func.abs(t.amount) >= lo - CENT] if lo is not None else []),
                *([func.abs(t.amount) <= hi + CENT] if hi is not None else []))


def _once_window(item: dict) -> tuple[str, str]:
    """A one-time item's matching window, first and last day: around the day its money moves (its date, moved off a
    weekend or holiday as the forecast moves it), so a payment the forecast still expects can match it."""
    window = timedelta(days=MATCH_WINDOW_DAYS["once"])
    day = bankdays.settles(dates.parse_day(item["anchor_date"]), (item.get("amount") or 0) > 0)
    return (day - window).isoformat(), (day + window).isoformat()


def near_date(item: dict, posted: str) -> bool:
    """Whether a payment posted on `posted` can be the item's: any day for a repeating item; for a one-time one, only
    within its matching window of its date (so a tax refund's "irs" doesn't claim every later payment to the IRS)."""
    if item.get("frequency") != "once":
        return True
    lo, hi = _once_window(item)
    return lo <= posted[:10] <= hi


def posted_near(item: dict):
    """SQL: near_date, for the transaction's posted date."""
    if item.get("frequency") != "once":
        return true()
    lo, hi = _once_window(item)
    return and_(func.substr(Transaction.posted, 1, 10) >= lo, func.substr(Transaction.posted, 1, 10) <= hi)


def auto_match(conn, recurring_ids: list[int] | None = None) -> int:
    """Link unlinked transactions to recurring items by merchant text: any of the item's texts, on its account, the
    same way the money moves (money-in items only match money in, money-out items money out), and within the item's
    amount range when it has one (so a "Prime" item matching "amazon" can leave the other orders alone). A one-time
    item only matches around its date (posted_near).
    Transactions marked 'never match' (recurring_id = 0) and ones already linked are left alone. When the item has a
    category, the ones linked now that have none of their own take it (source "recurring"); one that has its own keeps it."""
    q = select(Recurring).where(Recurring.active == 1)
    if recurring_ids:
        q = q.where(Recurring.id.in_(list(recurring_ids)))
    items = sorted(db.rows(conn.execute(q)), key=lambda r: -max(map(len, match_texts(r)), default=0))  # most specific first
    linked = 0
    t = Transaction
    for item in items:
        texts = [m for m in match_texts(item) if len(m) >= 3]
        if not texts:
            continue
        common = [t.recurring_id.is_(None), t.account_id == item["account_id"], t.amount > 0 if item["amount"] > 0 else t.amount < 0,
                  amount_fits(item), posted_near(item)]
        before = set(conn.execute(select(t.id).where(t.recurring_id == item["id"])).scalars()) if item["category"] else set()
        linked += conn.execute(update(t).where(*common, has_text(texts)).values(recurring_id=item["id"], recurring_linked_by="auto")).rowcount
        # A text that's a brand's name ("amazon") also finds that brand's transactions you've given the bank's name
        # ("Amzn Mktp Us"), by the brand their bank text gives.
        named = {m for m in texts if m in brands.BRAND_NAMES}
        if named:
            from .categorize import bank_payee   # here, not at the top: categorize imports this module's importers
            key = lambda s: " ".join((s or "").lower().split())
            ids = [r["id"] for r in conn.execute(select(t.id, t.payee, t.description).where(*common))
                   if key(r["payee"]) == key(bank_payee(r["description"]))   # still the bank's name, not one you gave it
                   and (brands.merchant_name(bank_payee(r["description"])) or "").lower() in named]
            if ids:
                linked += conn.execute(update(t).where(t.id.in_(ids), t.recurring_id.is_(None))
                                       .values(recurring_id=item["id"], recurring_linked_by="auto")).rowcount
        if item["category"]:
            now = set(conn.execute(select(t.id).where(t.recurring_id == item["id"])).scalars()) - before
            for chunk in _chunks(sorted(now)):
                conn.execute(update(t).where(t.id.in_(chunk), func.coalesce(t.category, "") == "", func.coalesce(t.is_split, 0) == 0)
                             .values(category=item["category"], category_source="recurring", confidence=1, needs_review=0))
    return linked


def _chunks(ids: list[str], size: int = 500):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def link(conn, tx_id: str, recurring_id: int | None) -> str | None:
    """Link a transaction to a recurring item (None = mark as not recurring). The item learns the merchant text from
    the transaction if it doesn't have one, so future payments match on their own. When it has some and none is on this
    transaction, and it's on the item's account, returns the transaction's text to offer as another (add_text); None
    otherwise. When the item has a category, the transaction takes it, whatever it had (unless it's split: its parts
    carry the categories); an item without one leaves the transaction's alone. Unlinking leaves the category as it is."""
    tx = _transaction(conn, tx_id)
    if not tx:
        raise ValueError("Transaction not found")
    if recurring_id is None:
        conn.execute(update(Transaction).where(Transaction.id == tx_id).values(recurring_id=0, recurring_linked_by="you"))
        return None
    item = conn.execute(select(Recurring).where(Recurring.id == recurring_id)).fetchone()
    if not item:
        raise ValueError("Recurring item not found")
    conn.execute(update(Transaction).where(Transaction.id == tx_id).values(recurring_id=recurring_id, recurring_linked_by="you"))
    if item["category"] and not tx["is_split"]:
        conn.execute(update(Transaction).where(Transaction.id == tx_id).values(
            category=item["category"], category_source="manual", confidence=1, needs_review=0))   # your choice, so it sticks
    text = " ".join((tx["payee"] or tx["description"] or "").lower().split())
    if not item["match"]:
        if text:
            conn.execute(update(Recurring).where(Recurring.id == recurring_id).values(match=text))
            auto_match(conn, [recurring_id])
        return None
    if tx["account_id"] != item["account_id"]:
        return None   # matching only looks on the item's own account, so the text wouldn't match anything there
    hay = f"{tx['payee'] or ''} {tx['description'] or ''}".lower()
    return text if len(text) >= 3 and not any(m in hay for m in match_texts(dict(item))) else None


def add_text(conn, recurring_id: int, text: str) -> int:
    """Add another merchant text to an item ("also match this from now on"), and link what it matches now. Returns how
    many were linked."""
    item = conn.execute(select(Recurring).where(Recurring.id == recurring_id)).fetchone()
    if not item:
        raise ValueError("Recurring item not found")
    m = " ".join(str(text or "").lower().split())
    if len(m) < 3:
        raise ValueError("Use at least three letters of text")
    texts = match_texts({"match": item["match"]}) if item["match"] else []
    conn.execute(update(Recurring).where(Recurring.id == recurring_id).values(match=clean_texts([*texts, m])))
    return auto_match(conn, [recurring_id])


def _transaction(conn, tx_id: str):
    return conn.execute(select(Transaction).where(Transaction.id == tx_id)).fetchone()


def create_from_transaction(conn, tx_id: str, frequency: str = "monthly") -> int:
    tx = _transaction(conn, tx_id)
    if not tx:
        raise ValueError("Transaction not found")
    name = tx["payee"] or tx["description"] or "Recurring item"
    rid = conn.execute(insert(Recurring).values(
        name=name, account_id=tx["account_id"], amount=tx["amount"], frequency=frequency, anchor_date=tx["posted"],
        match=name.lower(), amount_mode="fixed", amount_since=date.today().isoformat())).lastrowid
    conn.execute(update(Transaction).where(Transaction.id == tx_id).values(recurring_id=rid, recurring_linked_by="you"))
    auto_match(conn, [rid])
    return rid


def matched(conn, recurring_id: int, limit: int = 12) -> list[dict]:
    """The item's last `limit` matched payments, newest first."""
    return matched_by_item(conn, [recurring_id], limit)[recurring_id]


def matched_by_item(conn, recurring_ids: list[int], limit: int = 12) -> dict[int, list[dict]]:
    """matched() for each of these items, in one query: {item id: its last `limit` payments, newest first}."""
    t = Transaction
    n = func.row_number().over(partition_by=t.recurring_id, order_by=(t.posted.desc(), t.id)).label("n")
    sub = (select(t.recurring_id, t.id, t.posted, t.amount, t.description, t.pending, t.category, n)
           .where(t.recurring_id.in_(list(recurring_ids))).subquery())
    out: dict[int, list[dict]] = {rid: [] for rid in recurring_ids}
    for r in db.rows(conn.execute(select(sub).where(sub.c.n <= limit).order_by(sub.c.recurring_id, sub.c.n))):
        rid = r.pop("recurring_id")
        del r["n"]
        out.setdefault(rid, []).append(r)
    return out


def first_posted(conn, account_ids) -> dict[str, str]:
    """{account id: its first transaction's day}: where each account's synced history begins (none without any)."""
    t = Transaction
    ids = sorted({a for a in account_ids if a is not None})
    if not ids:
        return {}
    return {a: p for a, p in conn.execute(select(t.account_id, func.min(t.posted)).where(t.account_id.in_(ids)).group_by(t.account_id))}


def with_account_name():
    """Every recurring item's columns plus its account's name (account_name; None if the account is gone)."""
    return (select(Recurring, db.account_label_expr().label("account_name"))
            .outerjoin(Account, Account.id == Recurring.account_id))


def _same_way(item: dict, payments: list[dict]) -> list[dict]:
    """The payments that move money the item's way (money in for a paycheck, out for a bill); all of them for an item
    without an amount, which learns even its direction from them."""
    if is_zero(item["amount"] or 0):
        return payments
    return [p for p in payments if (p["amount"] > 0) == (item["amount"] > 0)]


def by_occurrence(item: dict, payments: list[dict]) -> list[tuple[date | None, list[dict]]]:
    """Payments (posted, amount) grouped by the occurrence each was for: the scheduled date nearest it within the
    matching window (the earlier on a tie, so two windows that overlap don't both count it). One outside every window
    is a group of its own, with no date. Newest group first."""
    from . import forecast   # forecast imports this module

    if not payments:
        return []
    window = MATCH_WINDOW_DAYS.get(item["frequency"], 6)
    days = [date.fromisoformat(p["posted"]) for p in payments]
    occs = forecast.occurrences(item, min(days) - timedelta(days=window + 1), max(days) + timedelta(days=window))
    groups: dict[date | int, list[dict]] = {}
    for i, (p, d) in enumerate(zip(payments, days, strict=True)):
        near = min(occs, key=lambda o: (abs((o - d).days), o), default=None)
        groups.setdefault(near if near is not None and abs((near - d).days) <= window else i, []).append(p)
    out = [(k if isinstance(k, date) else None, ps) for k, ps in groups.items()]
    return sorted(out, key=lambda g: max(p["posted"] for p in g[1]), reverse=True)


def paid_by_occurrence(item: dict, history: list[dict]) -> dict[date, float]:
    """What's been paid toward each occurrence (signed like the item's amount; pending payments count): the item's
    linked payments that move money its way, grouped by by_occurrence."""
    return {occ: round(sum(p["amount"] for p in ps), 2) for occ, ps in by_occurrence(item, _same_way(item, history)) if occ}


def still_due(item: dict, occurrence: date, paid: dict[date, float], today: date, amount: float,
              edited: bool = False) -> float | None:
    """What's still expected for an occurrence: `amount` until something's been paid toward it (`paid`, from
    paid_by_occurrence), then what's left, while its matching window is open, for a fixed amount or one you `edited`
    (see SHORTFALL). Paid to within SHORTFALL of the amount, or once the window has closed, nothing is (None): money
    that came up short isn't expected late."""
    window = MATCH_WINDOW_DAYS.get(item["frequency"], 6)
    if today > occurrence + timedelta(days=window):
        return None
    got = paid.get(occurrence, 0.0)
    if is_zero(got):
        return amount
    if edited:   # you said what this one comes to: whatever of that hasn't come yet is still expected, however small
        left = round(amount - got, 2)
        return left if left * amount > 0 else None
    if (item.get("amount_mode") or "fixed") != "fixed":
        return None   # a learned amount varies: whatever came is this time's
    if amount < 0 and abs(got) >= abs(amount) / 2:
        return None   # a bill that came in cheaper, not a first part
    left = round(amount - got, 2)
    return left if left * amount > 0 and abs(left) > SHORTFALL * abs(amount) else None


def expected_amount(item: dict, history: list[dict]) -> float:
    """Fixed amount, or learn it from recent real payments (handy for bills that vary): what the last occurrence came
    to, or the average of the last three, counting one paid in parts as its total."""
    mode = item.get("amount_mode") or "fixed"
    if mode in ("last", "avg3"):
        posted = [sum(p["amount"] for p in ps) for _, ps in by_occurrence(item, _same_way(item, [t for t in history if not t["pending"]]))]
        if posted:
            return round(posted[0] if mode == "last" else statistics.mean(posted[:3]), 2)
    return round(item["amount"], 2)


def stale_amount(item: dict, history: list[dict], today: date | None = None) -> float | None:
    """For a fixed-amount item whose latest payments all came to something else (a raise, a new rate), what they came to
    on average: the amount to suggest instead. It looks at the last RECENT_PAYMENTS occurrences paid since the amount was
    last set (each one's total, when it came in parts; not one whose window is still open, which may have more to come):
    at least two, all off the amount the same way by more than DRIFT_SHARE of it or DRIFT_DOLLARS (whichever is more), and
    close to each other. None otherwise."""
    amount = abs(item["amount"] or 0)
    if (item.get("amount_mode") or "fixed") != "fixed" or amount < CENT:
        return None
    today = today or date.today()
    window = timedelta(days=MATCH_WINDOW_DAYS.get(item["frequency"], 6))
    since = item.get("amount_since") or ""
    payments = _same_way(item, [t for t in history if not t["pending"] and t["posted"] >= since])
    totals = [abs(sum(p["amount"] for p in ps)) for occ, ps in by_occurrence(item, payments)
              if occ is None or occ + window < today][:RECENT_PAYMENTS]
    off = max(amount * DRIFT_SHARE, DRIFT_DOLLARS)
    lo, hi = amount - off, amount + off
    if len(totals) < 2 or not (all(t < lo for t in totals) or all(t > hi for t in totals)):
        return None
    level = statistics.mean(totals)
    if any(abs(t - level) > STALE_TOLERANCE * level for t in totals):
        return None   # all over the place: no one amount to offer
    return math.copysign(round(level, 2), item["amount"])


def skipped_keys(conn) -> set[str]:
    """The occurrences you've skipped ("Skip the next one", "Skip this one"): a one-off edit to $0 of a recurring
    item's date (rec:<id>:<date>), which the forecast already reads as nothing coming that day."""
    return {k for k, a in conn.execute(select(Override.key, Override.amount).where(Override.key.like("rec:%")))
            if is_zero(a or 0)}


CANDIDATES = 6
CANDIDATE_SHARE = 0.3


def candidates(conn, item: dict, day: date, amount: float) -> list[dict]:
    """Transactions that could be the payment the item missed on `day`, to link by hand: not linked to anything (nor
    marked not recurring), on its account, moving money its way, posted within twice its matching window of the day,
    and within CANDIDATE_SHARE of `amount` either way (any amount, for an item that learns its amount and has none yet).
    Closest in amount, then in date, first."""
    t = Transaction
    window = 2 * MATCH_WINDOW_DAYS.get(item["frequency"], 6)
    want = abs(amount)
    q = (select(t.id, t.posted, t.amount, t.payee, t.description, t.pending)
         .where(t.recurring_id.is_(None), t.account_id == item["account_id"],
                t.posted >= (day - timedelta(days=window)).isoformat(), t.posted <= (day + timedelta(days=window)).isoformat()))
    if want >= CENT:
        q = q.where(t.amount > 0 if amount > 0 else t.amount < 0, func.abs(t.amount) >= want * (1 - CANDIDATE_SHARE),
                    func.abs(t.amount) <= want * (1 + CANDIDATE_SHARE))
    rows = db.rows(conn.execute(q))
    rows.sort(key=lambda r: (round(abs(abs(r["amount"]) - want), 2), abs((dates.parse_day(r["posted"]) - day).days), r["id"]))
    return rows[:CANDIDATES]


LOOKBACK_DAYS = 60


def missed(conn, today: date | None = None, lookback: int = LOOKBACK_DAYS) -> list[dict]:
    """Scheduled occurrences in the last `lookback` days with no matching transaction by now.

    An occurrence counts as missed once its matching window has passed (e.g. 6 days for a monthly bill) without a
    linked transaction within that window either side of the date (one that came in short, or in parts, isn't missed:
    something arrived). Occurrences before the item's start date, before
    the account's synced history begins, or that you've dismissed or skipped are left out."""
    from . import forecast   # forecast imports this module

    today = today or date.today()
    dismissed = set(conn.execute(select(RecurringDismissed.key)).scalars()) | skipped_keys(conn)
    out = []
    t = Transaction
    items = db.rows(conn.execute(with_account_name().where(Recurring.active == 1)))
    firsts = first_posted(conn, [i["account_id"] for i in items])
    starts = {}
    for item in items:
        first_tx = firsts.get(item["account_id"])
        if first_tx:
            starts[item["id"]] = max(today - timedelta(days=lookback), date.fromisoformat(item["anchor_date"]) - timedelta(days=1),
                                     date.fromisoformat(first_tx) + timedelta(days=MATCH_WINDOW_DAYS.get(item["frequency"], 6)))
    # Every item's matched payments since the earliest of their starts (less 40 days), in one query: each item looks at
    # its own from its own start.
    since = (min(starts.values()) - timedelta(days=40)).isoformat() if starts else ""
    posted: dict[int, list[str]] = {}
    for rid, p in conn.execute(select(t.recurring_id, t.posted).where(t.recurring_id.in_(list(starts)), t.posted >= since)):
        posted.setdefault(rid, []).append(p)
    for item in items:
        if item["id"] not in starts:
            continue
        window = MATCH_WINDOW_DAYS.get(item["frequency"], 6)
        start = starts[item["id"]]
        hist = [p for p in posted.get(item["id"], []) if p >= (start - timedelta(days=40)).isoformat()]
        for occ in forecast.occurrences(item, start, today - timedelta(days=window + 1)):
            key = f"rec:{item['id']}:{occ.isoformat()}"
            if key in dismissed:
                continue
            lo, hi = (occ - timedelta(days=window)).isoformat(), (occ + timedelta(days=window)).isoformat()
            if any(lo <= p <= hi for p in hist):
                continue
            out.append({"key": key, "recurring_id": item["id"], "name": item["name"], "date": occ.isoformat(),
                        "amount": round(item["amount"], 2), "account_id": item["account_id"], "account_name": item["account_name"],
                        "match": item["match"], "window": window})
    out.sort(key=lambda m: m["date"], reverse=True)
    return out


def dismiss(conn, key: str) -> None:
    db.insert_ignore(conn, RecurringDismissed, {"key": key}, key=["key"])
