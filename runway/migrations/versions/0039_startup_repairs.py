"""Repairs Runway used to run every time it started, for data saved by earlier versions, done once here instead:

- categories nested deeper than a subcategory (briefly allowed) move up, directly under their top-level category;
- people signed in before the users list existed are added to it, from their sign-ins;
- SimpleFIN investment accounts saved before Runway kept what SimpleFIN sent get that feed rebuilt from their stored
  positions (sf_raw:<account id>), so the next sync's price check (sfinvest.recapture_all) runs them through today's
  checks; and names a brokerage's scraped page text got into ("keyboard_arrow_right ...") are cleared.

Revision ID: 0039
Revises: 0038
"""
import json

import sqlalchemy as sa
from alembic import op

revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None

MAX_DEPTH = 2   # levels including the top one: Food > Restaurants


def _flatten_categories(bind) -> None:
    parents = dict(bind.execute(sa.text("SELECT name, parent FROM categories")).fetchall())
    for name in sorted(parents):
        chain = [name]
        while parents.get(chain[-1]) in parents and parents[chain[-1]] not in chain:
            chain.append(parents[chain[-1]])
        if parents.get(chain[-1]) in chain:
            continue   # a loop: left alone (categories.path stops on it)
        if len(chain) > MAX_DEPTH:
            bind.execute(sa.text("UPDATE categories SET parent = :top WHERE name = :name"), {"top": chain[-1], "name": name})


def _first_name(name, email) -> str:
    """oidc.first_name, as it was (a sign-in kept no given name)."""
    if name and name.strip() and "@" not in name:
        return name.strip().split()[0]
    local = (email or "").split("@")[0]
    return (local.split(".")[0].split("_")[0] or "Someone").capitalize()


def _backfill_users(bind) -> None:
    rows = bind.execute(sa.text(
        "SELECT sub, email, name, MAX(created) AS t FROM auth_sessions WHERE sub IS NOT NULL "
        "AND sub NOT IN (SELECT sub FROM users) GROUP BY sub, email, name ORDER BY sub, MAX(created) DESC")).fetchall()
    done = set()
    for sub, email, name, t in rows:
        if sub in done:
            continue
        done.add(sub)
        bind.execute(sa.text("INSERT INTO users(sub, email, name, first_name, last_seen) VALUES (:sub, :email, :name, :first, :t)"),
                     {"sub": sub, "email": email, "name": name, "first": _first_name(name, email), "t": t})


def _simplefin_feeds(bind) -> None:
    bind.execute(sa.text("UPDATE securities SET name = NULL WHERE id LIKE 'sf:%' AND name LIKE '%keyboard_arrow%'"))
    accounts = bind.execute(sa.text(
        "SELECT i.id, i.name, i.currency, i.institution, i.balance FROM inv_accounts i "
        "WHERE i.source = 'simplefin' AND EXISTS (SELECT 1 FROM accounts a WHERE 'sf:' || a.id = i.id) "
        "AND NOT EXISTS (SELECT 1 FROM settings s WHERE s.key = 'sf_raw:' || substr(i.id, 4)) ORDER BY i.id")).fetchall()
    for iid, name, currency, org, balance in accounts:
        held = bind.execute(sa.text(
            "SELECT s.ticker, s.name, h.quantity, h.value, h.cost_basis FROM holdings h JOIN securities s ON s.id = h.security_id "
            "WHERE h.account_id = :iid AND h.security_id NOT IN ('sf:cash', 'sf:balance') ORDER BY h.security_id"), {"iid": iid})
        raw = [{"symbol": ticker or "", "description": sec or ticker or "", "shares": qty, "market_value": value, "cost_basis": cost}
               for ticker, sec, qty, value, cost in held]
        feed = {"acct": {"name": name, "currency": currency or "USD", "holdings": raw}, "org": org, "balance": balance or 0.0}
        bind.execute(sa.text("INSERT INTO settings(key, value) VALUES (:key, :value)"),
                     {"key": "sf_raw:" + iid[3:], "value": json.dumps(feed)})


def upgrade() -> None:
    bind = op.get_bind()
    _flatten_categories(bind)
    _backfill_users(bind)
    _simplefin_feeds(bind)


def downgrade() -> None:
    pass   # nothing to undo: each is what the version before did at start-up
