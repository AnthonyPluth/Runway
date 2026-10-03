"""Made-up sample data for a preview or a demo: a few accounts, six months of transactions, bills and paychecks,
budgets and a home. Nothing here is real, and seeding refuses to touch a database that already has accounts."""
import random
from datetime import date, timedelta

from sqlalchemy import func, insert, select, update

from . import db
from . import settings_keys as sk
from .models import Account, Asset, Budget, Category, ManualStatement, Recurring, SyncLog, Transaction

ACCOUNTS = [
    # id, name, org, kind, balance
    ("demo-checking", "Everyday Checking", "Sample Bank", "checking", 4820.55),
    ("demo-savings", "High-Yield Savings", "Sample Bank", "savings", 18250.00),
    ("demo-card", "Rewards Visa", "Sample Card Co", "credit", -1342.18),
    ("demo-mortgage", "Home Mortgage", "Sample Lending", "loan", -284900.00),
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


def seed(conn, today: date | None = None) -> int:
    """Fill an empty database with sample data. Returns the number of transactions added."""
    if conn.execute(select(func.count()).select_from(Account)).fetchone()[0]:
        raise SystemExit("This database already has accounts; sample data only goes into an empty one.")
    today = today or date.today()
    rnd = random.Random(42)   # the same sample every time
    for acct_id, name, org, kind, balance in ACCOUNTS:
        conn.execute(insert(Account).values(id=acct_id, name=name, org=org, kind=kind, balance=balance,
                                            balance_date=today.isoformat(), provider="simplefin",
                                            pay_from="demo-checking" if kind == "credit" else None,
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
    # The card's latest statement, entered by hand (a card without Plaid): it closes on the 28th, due 25 days later.
    close = today.replace(day=28) if today.day >= 28 else (today.replace(day=1) - timedelta(days=1)).replace(day=28)
    prev = (close.replace(day=1) - timedelta(days=1)).replace(day=28)
    owed = -sum(t[3] for t in txs if t[1] == "demo-card" and prev.isoformat() < t[2] <= close.isoformat() and t[6] != "Credit Card Payment")
    conn.execute(insert(ManualStatement).values(account_id="demo-card", statement_date=close.isoformat(), balance=round(owed, 2),
                                                due_date=(close + timedelta(days=25)).isoformat()))
    conn.execute(insert(Asset).values(name="Sample House", kind="home", value=415000, as_of=today.isoformat(), yearly_change=3,
                                      loan_account_id="demo-mortgage"))
    # The app shows its "connect your bank" screen until a bank is set up. This address never resolves (.invalid), so
    # a Sync in a preview just fails; nothing is ever fetched.
    db.set_setting(conn, sk.SIMPLEFIN_ACCESS_URL, "https://demo:demo@sample-bank.invalid/simplefin")
    conn.execute(insert(SyncLog).values(ok=1, message="Sample data"))
    return len(txs)
