"""Made-up sample data for a preview or a demo: a few accounts, six months of transactions, bills and paychecks,
budgets and a home. Nothing here is real, and seeding refuses to touch a database that already has accounts."""
import math
import random
from datetime import date, timedelta

from sqlalchemy import func, insert, select, update

from ..storage import db
from ..storage import settings_keys as sk
from .forecast import NO_STATEMENT_DUE_DAYS
from .retail import token as retail_token
from ..storage.models import (Account, Asset, Budget, Category, Holding, InvAccount, ManualStatement, Price, PriceMeta, Recurring,
                              RetailCharge, RetailItem, RetailOrder, Security, SyncLog, Transaction)

ACCOUNTS = [
    # id, name, org, kind, balance
    ("demo-checking", "Everyday Checking", "Sample Bank", "checking", 4820.55),
    ("demo-savings", "High-Yield Savings", "Sample Bank", "savings", 18250.00),
    ("demo-card", "Rewards Visa", "Sample Card Co", "credit", -1342.18),
    ("demo-mortgage", "Home Mortgage", "Sample Lending", "loan", -284900.00),
    # A second card with no account paying it yet, so Overview has a warning that can be put away.
    ("demo-travel", "Travel Mastercard", "Sample Card Co", "credit", -236.40),
]

# name, account, amount (negative = out), frequency, day of month (or weekday offset), payee match, category
BILLS = [
    ("Paycheck", "demo-checking", 3150.00, "biweekly", None, "acme corp payroll", "Income"),
    ("Mortgage", "demo-checking", -2140.00, "monthly", 1, "sample lending mortgage", "Mortgage"),
    ("Electric", "demo-checking", -118.00, "monthly", 12, "city power & light", "Utilities"),
    ("Internet", "demo-card", -70.00, "monthly", 8, "fastnet internet", "Utilities"),
    ("Streaming", "demo-card", -15.49, "monthly", 20, "streamflix", "Subscriptions"),
    ("Music", "demo-card", -10.99, "monthly", 3, "tuneworld", "Subscriptions"),
    ("Gym", "demo-card", -45.00, "monthly", 5, "fit club", "Extra-Curriculars"),
]

# payee, category, low, high, times per month
EVERYDAY = [
    ("Green Grocer Market", "Groceries", 45, 160, 5),
    ("Corner Coffee", "Coffee & Snacks", 4, 9, 8),
    ("Taco Place", "Restaurants", 18, 55, 3),
    ("Pizza Palace", "Restaurants", 22, 48, 2),
    ("Fuel Stop", "Auto & Gas", 35, 60, 2),
    ("Online Store", "Shopping", 12, 140, 3),
    ("Hardware Depot", "Home Improvement", 20, 180, 1),
    ("City Pharmacy", "Pharmacy", 8, 40, 1),
]

BUDGETS = [("Groceries", 600), ("Restaurants", 250), ("Coffee & Snacks", 60), ("Shopping", 300)]

# ticker, name, type, shares, price paid per share, yesterday's close, today's close: one up, one down, one barely moved
HOLDINGS = [
    ("VTI", "Sample Total Market ETF", "etf", 42.0, 205.00, 281.40, 284.95),
    ("VXUS", "Sample International ETF", "etf", 60.0, 61.00, 68.20, 67.55),
    ("SMPL", "Sample Industries Inc", "equity", 15.0, 140.00, 192.10, 192.15),
]

PART_PAYMENT = 400.0   # paid toward the card's latest statement a few days after it closed (at most half of it)


def seed(conn, today: date | None = None) -> int:
    """Fill an empty database with sample data. Returns the number of transactions added."""
    if conn.execute(select(func.count()).select_from(Account)).fetchone()[0]:
        raise SystemExit("This database already has accounts; sample data only goes into an empty one.")
    today = today or date.today()
    rnd = random.Random(42)   # the same sample every time
    for acct_id, name, org, kind, balance in ACCOUNTS:
        conn.execute(insert(Account).values(id=acct_id, name=name, org=org, kind=kind, balance=balance,
                                            balance_date=today.isoformat(), provider="simplefin",
                                            pay_from="demo-checking" if acct_id == "demo-card" else None,
                                            in_forecast=1 if kind in ("checking", "savings") else 0))

    txs: list[tuple] = []
    start = today - timedelta(days=180)

    def add(acct, day, amount, payee, category):
        if start <= day <= today:
            txs.append((f"{acct}|demo-{len(txs)}", acct, day.isoformat(), round(amount, 2), payee.upper(), payee, category))

    first_payday = start + timedelta(days=(4 - start.weekday()) % 7)   # Fridays
    for name, acct, amount, freq, dom, match, category in BILLS:
        anchor = first_payday if freq == "biweekly" else start.replace(day=dom)  # type: ignore[arg-type]  # monthly bills all have a day
        conn.execute(insert(Recurring).values(name=name, account_id=acct, amount=amount, frequency=freq,
                                              anchor_date=anchor.isoformat(), match=match))
        day = anchor
        while day <= today:
            add(acct, day, amount * (1 + rnd.uniform(-0.08, 0.08) if category == "Utilities" else 1), match.title(), category)
            if freq == "biweekly":
                day += timedelta(days=14)
            else:
                day = (day.replace(day=1) + timedelta(days=32)).replace(day=dom)  # type: ignore[arg-type]  # as above

    for offset in range(181):
        day = start + timedelta(days=offset)
        for payee, category, low, high, per_month in EVERYDAY:
            if rnd.random() < per_month / 30:
                add("demo-card", day, -rnd.uniform(low, high), payee, category)
        if day.day == 25:   # pay the card, move some to savings
            add("demo-checking", day, -1400, "Rewards Visa Payment", "Credit Card Payment")
            add("demo-card", day, 1400, "Payment Thank You", "Credit Card Payment")
            add("demo-checking", day, -500, "Transfer to Savings", "Transfer")
            add("demo-savings", day, 500, "Transfer from Checking", "Transfer")

    cols = ("id", "account_id", "posted", "amount", "description", "payee", "category")
    conn.execute(insert(Transaction), [{**dict(zip(cols, t, strict=True)), "category_source": "rule"} for t in txs])
    conn.execute(insert(Budget), [{"category": c, "amount": a} for c, a in BUDGETS])
    conn.execute(update(Category).where(Category.name.in_([c for c, _ in BUDGETS])).values(pay_with="demo-card"))
    # The card's latest statement, entered by hand (a card without Plaid): it closes on the 28th, due NO_STATEMENT_DUE_DAYS
    # later (as the forecast takes a statement with no due date to be).
    close = today.replace(day=28) if today.day >= 28 else (today.replace(day=1) - timedelta(days=1)).replace(day=28)
    prev = (close.replace(day=1) - timedelta(days=1)).replace(day=28)
    owed = -sum(t[3] for t in txs if t[1] == "demo-card" and prev.isoformat() < t[2] <= close.isoformat() and t[6] != "Credit Card Payment")
    conn.execute(insert(ManualStatement).values(account_id="demo-card", statement_date=close.isoformat(), balance=round(owed, 2),
                                                due_date=(close + timedelta(days=NO_STATEMENT_DUE_DAYS)).isoformat()))
    # A part payment a few days after it closed (once that day has come), so the card has less left to pay than its
    # statement.
    paid_on = close + timedelta(days=3)
    if paid_on <= today and owed > 0:
        part = round(min(PART_PAYMENT, owed / 2), 2)
        conn.execute(insert(Transaction), [
            {"id": f"{acct}|demo-part", "account_id": acct, "posted": paid_on.isoformat(), "amount": amount,
             "description": payee.upper(), "payee": payee, "category": "Credit Card Payment", "category_source": "rule"}
            for acct, amount, payee in (("demo-checking", -part, "Rewards Visa Payment"), ("demo-card", part, "Payment Thank You"))])
    conn.execute(insert(ManualStatement).values(account_id="demo-travel", statement_date=close.isoformat(), balance=236.40,
                                                due_date=(close + timedelta(days=NO_STATEMENT_DUE_DAYS)).isoformat()))
    conn.execute(insert(Asset).values(name="Sample House", kind="home", value=415000, as_of=today.isoformat(), yearly_change=3,
                                      loan_account_id="demo-mortgage"))
    # The app shows its "connect your bank" screen until a bank is set up. This address never resolves (.invalid), so
    # a Sync in a preview just fails; nothing is ever fetched.
    db.set_setting(conn, sk.SIMPLEFIN_ACCESS_URL, "https://demo:demo@sample-bank.invalid/simplefin")
    conn.execute(insert(SyncLog).values(ok=1, message="Sample data"))
    return len(txs)


def seed_ai_buttons(conn, today: date | None = None) -> None:
    """Opt-in, on top of `seed` (`run.py demo --ai-buttons`, which `make verify` passes): what the AI buttons need to show in
    a screenshot, so a plain demo never looks AI-enabled. Something waiting in To review, an order whose items have no
    category, an OpenRouter key and the browser extension's key. The key is made up and nothing here sends anything: the
    flows only look at the buttons, never click them."""
    today = today or date.today()
    for i, (payee, amount) in enumerate([("Corner Market", -23.40), ("Lakeside Cafe", -8.75), ("Hardware Depot", -61.20)]):
        conn.execute(insert(Transaction).values(id=f"demo-card|demo-review-{i}", account_id="demo-card", amount=amount,
                                                posted=(today - timedelta(days=i + 1)).isoformat(), description=payee.upper(),
                                                payee=payee, needs_review=1))
    order, placed = "amazon:111-0000000-0000001", (today - timedelta(days=3)).isoformat()
    conn.execute(insert(RetailOrder).values(id=order, retailer="amazon", order_number="111-0000000-0000001", channel="online",
                                            placed=placed, total=53.97, details=1, payment="Visa 1234"))
    conn.execute(insert(RetailItem), [
        {"order_id": order, "position": 0, "title": "Bamboo cutting board", "quantity": 1, "amount": 24.99},
        {"order_id": order, "position": 1, "title": "Desk lamp with USB port", "quantity": 1, "amount": 28.98}])
    conn.execute(insert(RetailCharge).values(id=f"{order}|1", order_id=order, date=placed, amount=-53.97, payment="Visa 1234"))
    retail_token.new_token(conn, {"sub": "demo", "email": "demo@example.invalid"})   # with an owner, as a real key has
    db.set_setting(conn, sk.OPENROUTER_API_KEY, "sk-or-demo-not-a-real-key")


def seed_investments(conn, today: date) -> None:
    """A brokerage account with three holdings and a year of closes, so the Investments page has a day's gain, a day's
    loss and a nearly flat day to show."""
    total = 0.0
    conn.execute(insert(InvAccount).values(id="demo-brokerage", item_id="demo-item", name="Sample Brokerage", type="investment",
                                           subtype="brokerage", mask="0000", currency="USD", source="plaid",
                                           institution="Sample Brokerage Co", account_id="pl:demo-brokerage"))
    for ticker, name, kind, shares, paid, before, close in HOLDINGS:
        value = round(shares * close, 2)
        total += value
        conn.execute(insert(Security).values(id=f"demo-{ticker}", ticker=ticker, name=name, type=kind, is_cash=0, close_price=close,
                                             close_as_of=today.isoformat()))
        conn.execute(insert(Holding).values(account_id="demo-brokerage", security_id=f"demo-{ticker}", quantity=shares, price=close,
                                            price_as_of=today.isoformat(), value=value, cost_basis=round(shares * paid, 2)))
        # A year of closes before yesterday's: a steady climb with a wobble, so the page's charts have something to draw.
        closes = [(today - timedelta(days=k), round(before * (1 - 0.15 * k / 365) * (1 + 0.012 * math.sin(k / 5 + len(ticker))), 2))
                  for k in range(2, 366)]
        closes += [(today - timedelta(days=1), before), (today, close)]
        conn.execute(insert(Price), [{"ticker": ticker, "date": d.isoformat(), "close": c, "adjclose": c} for d, c in closes])
        conn.execute(insert(PriceMeta).values(ticker=ticker, fetched_at=f"{today.isoformat()}T00:00:00", ok=1, splits="[]"))
    conn.execute(update(InvAccount).where(InvAccount.id == "demo-brokerage").values(balance=round(total, 2)))
