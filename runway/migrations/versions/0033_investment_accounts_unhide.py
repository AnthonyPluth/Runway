"""Investments no longer has a way to leave an account out: hide it in Settings → Accounts instead.

inv_accounts.hidden used to be the Investments page's own "leave this account out" box. Nothing sets it from the page any
more, so accounts left out that way show again (unless they're hidden in Settings → Accounts, which the page still
honors). What stays is the one thing Runway itself marks: a SimpleFIN account that a Plaid connection also sends, so it
isn't counted twice (Plaid has the fuller data). Those are found the way Runway found them when it marked them, by
institution (or by the account Plaid's was matched to).

Revision ID: 0033
Revises: 0032
"""
import re

import sqlalchemy as sa
from alembic import op

revision = '0033'
down_revision = '0032'
branch_labels = None
depends_on = None

_NOISE = re.compile(r"\b(financial|investments?|securities|bank|inc|llc)\b")


def _compact(value) -> str:
    # runway/plaid.py hide_simplefin_duplicates as it was when this migration was written
    return re.sub(r"[^a-z0-9]", "", _NOISE.sub("", (value or "").lower()))


def upgrade() -> None:
    bind = op.get_bind()
    if 'inv_accounts' not in sa.inspect(bind).get_table_names():
        return
    rows = bind.execute(sa.text("SELECT id, source, institution, account_id FROM inv_accounts WHERE hidden = 1")).fetchall()
    if not rows:
        return
    plaid_keys = [k for (name,) in bind.execute(sa.text("SELECT institution_name FROM plaid_items")) if len(k := _compact(name)) >= 4]
    plaid_matched = {a for (a,) in bind.execute(sa.text("SELECT account_id FROM inv_accounts WHERE source = 'plaid' AND account_id IS NOT NULL"))}
    for r in rows:
        if r.source == 'simplefin':
            inst = _compact(r.institution)
            if r.id[3:] in plaid_matched or (len(inst) >= 4 and any(k in inst or inst in k for k in plaid_keys)):
                continue   # still a duplicate of a Plaid account
        bind.execute(sa.text("UPDATE inv_accounts SET hidden = 0 WHERE id = :id"), {"id": r.id})


def downgrade() -> None:
    pass   # which accounts you had left out isn't kept
