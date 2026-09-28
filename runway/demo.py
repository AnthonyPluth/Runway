"""Made-up sample data for a preview or a demo: a few accounts, six months of transactions, bills and paychecks,
budgets and a home. Nothing here is real, and seeding refuses to touch a database that already has accounts."""
import random
from datetime import date, timedelta

from . import db

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
    if conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:
        raise SystemExit("This database already has accounts; sample data only goes into an empty one.")
    today = today or date.today()
    rnd = random.Random(42)   # the same sample every time
    for acct_id, name, org, kind, balance in ACCOUNTS:
        conn.execute("INSERT INTO accounts(id, name, org, kind, balance, balance_date, provider, pay_from, in_forecast) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (acct_id, name, org, kind, balance, today.isoformat(), "simplefin",
                      "demo-checking" if kind == "credit" else None, 1 if kind in ("checking", "savings") else 0))

    txs = []
    start = today - timedelta(days=180)

    def add(acct, day, amount, payee, category):
        if start <= day <= today:
            txs.append((f"{acct}|demo-{len(txs)}", acct, day.isoformat(), round(amount, 2), payee.upper(), payee, category))

    first_payday = start + timedelta(days=(4 - start.weekday()) % 7)   # Fridays
    for name, acct, amount, freq, dom, match, category in BILLS:
        anchor = first_payday if freq == "biweekly" else start.replace(day=dom)
        conn.execute("INSERT INTO recurring(name, account_id, amount, frequency, anchor_date, match) VALUES (?,?,?,?,?,?)",
                     (name, acct, amount, freq, anchor.isoformat(), match))
        day = anchor
        while day <= today:
            add(acct, day, amount * (1 + rnd.uniform(-0.08, 0.08) if category == "Utilities" else 1), match.title(), category)
            if freq == "biweekly":
                day += timedelta(days=14)
            else:
                day = (day.replace(day=1) + timedelta(days=32)).replace(day=dom)

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

    conn.executemany("INSERT INTO transactions(id, account_id, posted, amount, description, payee, category, category_source) "
                     "VALUES (?,?,?,?,?,?,?,'rule')", txs)
    conn.executemany("INSERT INTO budgets(category, amount, pay_with) VALUES (?,?,'demo-card')", BUDGETS)
    conn.execute("INSERT INTO assets(name, kind, value, as_of, yearly_change, loan_account_id) VALUES (?,?,?,?,?,?)",
                 ("Sample House", "home", 415000, today.isoformat(), 3, "demo-mortgage"))
    # The app shows its "connect your bank" screen until a bank is set up. This address never resolves (.invalid), so
    # a Sync in a preview just fails; nothing is ever fetched.
    db.set_setting(conn, "simplefin_access_url", "https://demo:demo@sample-bank.invalid/simplefin")
    conn.execute("INSERT INTO sync_log(ok, message) VALUES (1, 'Sample data')")
    return len(txs)
