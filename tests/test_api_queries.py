"""The pages that list things (Accounts, Recurring, the app's state) ask the database a fixed number of questions, not
a few more for every account or item; and they answer just as they did when they asked row by row (the reference
versions below are those pages as they were, built from the per-row functions, which are still there)."""
import json
import os
from datetime import date, timedelta
from unittest import mock

from sqlalchemy import event, func, insert, select

from runway import brands, categorize, db, demo, forecast, loans, merchants, plaid, plaidbank, realie, recurring, statements
from runway.banks import bank_configured
from runway import settings_keys as sk
from runway.models import (Account, Budget, CardStatement, Override, PlaidAccount, PlaidItem, Recurring, RecurringDismissed, SyncLog,
                           Transaction)
from runway.server import routes
from runway.server.api import accounts, recurring as api_recurring, state
from tests.shared import TODAY, DbCase, freeze_today

# (date.today() is frozen at shared.TODAY in each test's setUp: the handlers read the clock too)



def old_card_statement(conn, card, institution, today=None):
    today = today or date.today()
    st = plaidbank.statement(conn, card["id"], today) if card.get("plaid_account_id") else None
    if st:
        return {"source": "plaid", "institution": institution, "closed": st["last_statement_date"], "due": st["next_due_date"],
                "balance": st["last_statement_balance"], "minimum": st["minimum_payment"]}
    m = statements.latest(conn, card["id"], today)
    if not m:
        return None
    return {"source": "manual", "closed": m["last_statement_date"], "due": m["next_due_date"], "balance": m["last_statement_balance"],
            "minimum": m["minimum_payment"], "stale": m["stale"], "next_close": m["next_close"]}


def old_api_accounts(conn):
    accts = db.rows(conn.execute(
        select(Account).order_by(Account.hidden, Account.kind, func.coalesce(Account.display_name, Account.name))))
    p, s = PlaidAccount, CardStatement
    items = {r["plaid_account_id"]: r for r in db.rows(conn.execute(
        select(p.plaid_account_id, p.mask, p.item_id, PlaidItem.products, PlaidItem.institution_name, s.last_statement_date,
               s.next_due_date, s.purchase_apr)
        .join(PlaidItem, PlaidItem.item_id == p.item_id).outerjoin(s, s.plaid_account_id == p.plaid_account_id)))}
    for a in accts:
        it = items.get(a.get("plaid_account_id") or "")
        a["plaid_link"] = ({"institution": it["institution_name"], "mask": it["mask"],
                            "transactions": "transactions" in (it["products"] or ""),
                            "closed": it["last_statement_date"], "due": it["next_due_date"],
                            "statement_note": db.get_setting(conn, sk.plaid_stmt_note(it['item_id']))} if it else None)
        if a["kind"] == "credit":
            a["statement"] = old_card_statement(conn, a, it["institution_name"] if it else None)
            a["statements"] = statements.history(conn, a["id"])
            plan = forecast.payment_plan(conn, a["id"])
            a.update(pay_mode=plan["pay_mode"], pay_amount=plan["pay_amount"], apr=plan["apr"],
                     issuer_apr=it["purchase_apr"] if it else None)
    terms = loans.terms(conn, date.today())
    for a in accts:
        if a["id"] in terms:
            a["loan"] = terms[a["id"]]
    return accts


def old_missed(conn, today, lookback=recurring.LOOKBACK_DAYS):
    dismissed = set(conn.execute(select(RecurringDismissed.key)).scalars()) | recurring.skipped_keys(conn)
    out = []
    t = Transaction
    for item in db.rows(conn.execute(recurring.with_account_name().where(Recurring.active == 1))):
        window = recurring.MATCH_WINDOW_DAYS.get(item["frequency"], 6)
        first_tx = conn.execute(select(func.min(t.posted)).where(t.account_id == item["account_id"])).scalar()
        if not first_tx:
            continue
        start = max(today - timedelta(days=lookback), date.fromisoformat(item["anchor_date"]) - timedelta(days=1),
                    date.fromisoformat(first_tx) + timedelta(days=window))
        hist = conn.execute(select(t.posted).where(t.recurring_id == item["id"],
                                                   t.posted >= (start - timedelta(days=40)).isoformat())).scalars()
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


def old_due(conn, it, hist, today, skipped):
    window = recurring.MATCH_WINDOW_DAYS.get(it["frequency"], 6)
    first_tx = conn.execute(select(func.min(Transaction.posted)).where(Transaction.account_id == it["account_id"])).scalar()
    since = max(today - timedelta(days=window + 1), date.fromisoformat(first_tx[:10]) + timedelta(days=window) if first_tx else today)
    paid = recurring.paid_by_occurrence(it, hist)
    due, skips = [], []
    for d in forecast.occurrences(it, min(since, today - timedelta(days=1)), today + timedelta(days=400)):
        if f"rec:{it['id']}:{d.isoformat()}" in skipped:
            skips.append(d)
        elif recurring.still_due(it, d, paid, today, it["expected_amount"]) is not None:
            due.append(d)
    late = [d for d in due if d < today]
    nxt = [d for d in due if d >= today]
    return {"next_date": nxt[0].isoformat() if nxt else None, "late_date": late[0].isoformat() if late else None,
            "skipped": [d.isoformat() for d in skips if d >= today]}


def old_api_recurring(conn):
    items = db.rows(conn.execute(recurring.with_account_name().order_by(Recurring.active.desc(), Recurring.name)))
    today = date.today()
    skipped = recurring.skipped_keys(conn)
    for it in items:
        hist = recurring.matched(conn, it["id"], 12)
        it["matched_count"] = conn.execute(
            select(func.count()).select_from(Transaction).where(Transaction.recurring_id == it["id"])).scalar()
        it["last_matched"] = hist[0] if hist else None
        it["expected_amount"] = recurring.expected_amount(it, hist)
        it["suggested_amount"] = recurring.stale_amount(it, hist, today)
        it.update(old_due(conn, it, hist, today, skipped))
    logos = api_recurring.recurring_logos(conn, items)
    missed = old_missed(conn, today)
    for it in items:
        it["logo"] = logos.get(it["id"])
        it["missed"] = [m for m in missed if m["recurring_id"] == it["id"]]
    return items


def old_api_state(conn):
    last_log = conn.execute(select(SyncLog.at, SyncLog.ok, SyncLog.message).order_by(SyncLog.id.desc()).limit(1)).fetchone()
    return {
        "connected": bank_configured(conn),
        "brands": brands.account_brands(conn),
        "connection_logos": brands.connection_logos(conn),
        "simplefin": bool(db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL)),
        "has_api_key": bool(db.get_setting(conn, sk.OPENROUTER_API_KEY)),
        "llm_model": categorize.llm_model(conn),
        "card_ai_model": categorize.card_ai_model(conn),
        "llm_model_default": categorize.DEFAULT_MODEL, "card_ai_model_default": categorize.DEFAULT_CARD_MODEL,
        "last_sync_ok": state.with_offset(db.get_setting(conn, sk.LAST_SYNC_OK)),
        "last_log": {**dict(last_log), "at": state.with_offset(last_log["at"], utc=True)} if last_log else None,
        "sync_warnings": json.loads(db.get_setting(conn, sk.LAST_SYNC_WARNINGS) or "[]"),
        "last_llm_error": db.get_setting(conn, sk.LAST_LLM_ERROR),
        "last_backup": state.with_offset(db.get_setting(conn, sk.LAST_BACKUP)),
        "review_count": conn.execute(select(func.count()).select_from(Transaction)
                                     .where(Transaction.needs_review == 1, db.not_investment())).fetchone()[0],
        "plaid_undecided": plaid.undecided_count(conn),
        "horizon_days": int(db.get_setting(conn, sk.HORIZON_DAYS, "90") or 90),
        "syncing": False,
        "primary_account": db.get_setting(conn, sk.PRIMARY_ACCOUNT),
        "auto_ai_on_sync": (db.get_setting(conn, sk.AUTO_AI_ON_SYNC, "1") or "1") == "1",
        "churn_ai_web": (db.get_setting(conn, sk.CHURN_AI_WEB, "1") or "1") == "1",
        "realie_configured": realie.configured(conn),
        "finnhub_configured": bool(db.get_setting(conn, sk.FINNHUB_API_KEY)),
        "logodev_configured": merchants.configured(conn),
        "database": "postgres" if db.using_postgres() else "sqlite",
        "version": os.environ.get("RUNWAY_VERSION") or "dev",
        "sentry": None,
        "owners": state.owner_choices(conn),
        "user": None,
        "setup": old_setup_steps(conn),
    }


def old_setup_steps(conn):
    checking = conn.execute(select(func.count()).select_from(Account)
                            .where(Account.hidden == 0, Account.kind == "checking")).fetchone()[0]
    return {
        "bank": bank_configured(conn) and bool(conn.execute(select(Account.id).limit(1)).fetchone()),
        "primary": bool(db.get_setting(conn, sk.PRIMARY_ACCOUNT)) or checking == 1,
        "recurring": bool(conn.execute(select(Recurring.id).limit(1)).fetchone()),
        "budgets": bool(conn.execute(select(Budget.category).where(Budget.amount > 0).limit(1)).fetchone()),
        "dismissed": db.get_setting(conn, sk.SETUP_DISMISSED) == "1",
    }



class Queries:
    """Counts the statements a connection runs."""

    def __init__(self, conn):
        self.n = 0
        event.listen(conn.sa, "before_cursor_execute", self.count)
        self.conn = conn

    def count(self, *_a):
        self.n += 1

    def of(self, fn) -> int:
        before = self.n
        fn()
        return self.n - before


class ListPageTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)
        self.cards = 0
        self.add(3)

    def add(self, n: int) -> None:
        """n more of everything a list page has a row for: cards (from Plaid, with statements, and entered by hand, with
        a payment plan), and recurring items with payments, a skipped date and a dismissed miss."""
        c = self.c
        for _ in range(n):
            i = self.cards = self.cards + 1
            item, pa = f"item-{i}", f"pa-{i}"
            c.execute(insert(PlaidItem).values(item_id=item, access_token="x", institution_name=f"Card Bank {i}",
                                               products="transactions,liabilities"))
            c.execute(insert(PlaidAccount).values(plaid_account_id=pa, item_id=item, mask=f"{1000 + i}"))
            c.execute(insert(CardStatement).values(plaid_account_id=pa, item_id=item, purchase_apr=20 + i,
                                                   last_statement_date=(TODAY - timedelta(days=10)).isoformat(),
                                                   next_due_date=(TODAY + timedelta(days=15)).isoformat(),
                                                   last_statement_balance=100.0 * i, minimum_payment=25))
            c.execute(insert(Account).values(id=f"plaid-card-{i}", name=f"Plaid Card {i}", kind="credit", balance=-50.0 * i,
                                             plaid_account_id=pa, provider="plaid"))
            db.set_setting(c, sk.plaid_stmt_note(item), f"note {i}")
            c.execute(insert(Account).values(id=f"hand-card-{i}", name=f"Hand Card {i}", kind="credit", balance=-20.0 * i))
            statements.add(c, f"hand-card-{i}", {"statement_date": (TODAY - timedelta(days=5 + i)).isoformat(),
                                                 "due_date": (TODAY + timedelta(days=20)).isoformat(), "balance": 80 + i})
            db.set_setting(c, sk.card_pay_mode(f"hand-card-{i}"), "fixed")
            db.set_setting(c, sk.card_pay_amount(f"hand-card-{i}"), str(40 + i))
            db.set_setting(c, sk.card_apr(f"plaid-card-{i}"), "19.9")
            rid = c.execute(insert(Recurring).values(name=f"Club {i}", account_id=f"hand-card-{i}", amount=-9.0 - i,
                                                     frequency="monthly", anchor_date=(TODAY - timedelta(days=95)).isoformat(),
                                                     match=f"club {i}")).lastrowid
            for k in range(3):
                c.execute(insert(Transaction).values(id=f"hand-card-{i}|club-{k}", account_id=f"hand-card-{i}",
                                                     posted=(TODAY - timedelta(days=95 - 30 * k)).isoformat(), amount=-9.0 - i,
                                                     payee=f"Club {i}", recurring_id=rid, pending=0))
            c.execute(insert(Override).values(key=f"rec:{rid}:{(TODAY + timedelta(days=30)).isoformat()}", amount=0))
            c.execute(insert(RecurringDismissed).values(key=f"rec:{rid}:{(TODAY - timedelta(days=35)).isoformat()}"))

    def same(self, a, b):
        self.assertEqual(json.dumps(a, sort_keys=False, default=str), json.dumps(b, sort_keys=False, default=str))

    def test_accounts_answer_as_before(self):
        self.same(accounts.api_accounts(self.c, {}, {}), old_api_accounts(self.c))

    def test_recurring_answers_as_before(self):
        self.same(api_recurring.api_recurring(self.c, {}, {}), old_api_recurring(self.c))
        self.same(recurring.missed(self.c, TODAY), old_missed(self.c, TODAY))

    def test_state_answers_as_before(self):
        for k, v in ((sk.SIMPLEFIN_ACCESS_URL, "https://u:p@bridge.example/simplefin"), (sk.OPENROUTER_API_KEY, "sk-or-x"),
                     (sk.HORIZON_DAYS, "120"), (sk.AUTO_AI_ON_SYNC, "0"), (sk.LAST_SYNC_WARNINGS, '["Bank: hello"]'),
                     (sk.PRIMARY_ACCOUNT, "demo-checking"), (sk.LLM_MODEL, "some/model"), (sk.LAST_BACKUP, "2026-09-01T10:00:00")):
            db.set_setting(self.c, k, v)
        now = state.api_state(self.c, {}, {})
        self.same(now, old_api_state(self.c))
        self.assertEqual((now["simplefin"], now["has_api_key"], now["horizon_days"], now["auto_ai_on_sync"],
                          now["llm_model"], now["finnhub_configured"]), (True, True, 120, False, "some/model", False))
        self.assertEqual(now["setup"]["primary"], True)
        self.same(state.api_state(self.c, {}, {}), old_api_state(self.c))

    def test_the_number_of_queries_doesnt_grow_with_the_rows(self):
        q = Queries(self.c)
        pages = {"accounts": lambda: accounts.api_accounts(self.c, {}, {}),
                 "recurring": lambda: api_recurring.api_recurring(self.c, {}, {}),
                 "missed": lambda: recurring.missed(self.c, TODAY),
                 "state": lambda: state.api_state(self.c, {}, {})}
        few = {name: q.of(fn) for name, fn in pages.items()}
        self.add(6)
        many = {name: q.of(fn) for name, fn in pages.items()}
        self.assertEqual(many, few)


class SettingsTests(DbCase):
    def setUp(self):
        super().setUp()
        freeze_today(self)

    def test_get_settings_is_get_setting_for_each(self):
        db.set_setting(self.c, sk.HORIZON_DAYS, "45")
        db.set_setting(self.c, sk.OPENROUTER_API_KEY, "sk-or-secret")
        db.set_setting(self.c, sk.LAST_LLM_ERROR, "")
        keys = [sk.HORIZON_DAYS, sk.OPENROUTER_API_KEY, sk.LAST_LLM_ERROR, sk.PRIMARY_ACCOUNT]
        self.assertEqual(db.get_settings(self.c, keys), {k: db.get_setting(self.c, k) for k in keys})
        self.assertEqual(db.get_settings(self.c, keys)[sk.OPENROUTER_API_KEY], "sk-or-secret")
        self.assertEqual(db.get_settings(self.c, []), {})

    def test_a_secret_counts_as_set_only_if_it_can_be_read(self):
        with mock.patch.dict(os.environ, {"RUNWAY_SECRET_KEY": "another-machines-key-abcdefghijklmnopqrstuvwxyz"}):
            db.set_setting(self.c, sk.OPENROUTER_API_KEY, "sk-or-elsewhere")
            db.set_setting(self.c, sk.FINNHUB_API_KEY, "finnhub-elsewhere")
        db.set_setting(self.c, sk.SIMPLEFIN_ACCESS_URL, "https://u:p@bridge.example/simplefin")
        db.set_setting(self.c, sk.LOGODEV_TOKEN, "pk_readable_here")
        self.c.commit()
        with mock.patch("builtins.print"):
            got = routes.dispatch(routes.match("GET", "/api/state"), {}, {})
        self.assertEqual((got["has_api_key"], got["finnhub_configured"], got["simplefin"], got["logodev_configured"],
                          got["realie_configured"], got["connected"]), (False, False, True, True, False, True))
