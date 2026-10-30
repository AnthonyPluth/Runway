"""Amazon charges saved again and again by the import that read the same payments page over and over: Amazon's newer
Transactions page answered the extension's request for its next page with its first page, so the extension read that
page until it stopped at its page limit (60), and each time Runway saved every charge on it under the next number
(`amazon|<order>|<day>|<cents>|1` up to `|60`). An order charged once that day so has 60 charges, one charged twice the
same amount that day 120. Runway now notices a page it has already read (retail.amazon_transactions).

For each charge with 60 copies or more, the first ones are kept (one for each 60, as many as were really charged) and the
rest removed, except a copy that's matched to a transaction or gave it a split or category, which stays as it is. Fewer
than 60 copies are left alone: those come from imports that read each page once.

Going back down changes nothing: the removed copies are gone.

Revision ID: 0049
Revises: 0048
"""
import math

import sqlalchemy as sa
from alembic import op

revision = '0049'
down_revision = '0048'
branch_labels = None
depends_on = None

PAGES = 60   # how many times the extension read the page (its MAX_PAGES)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, tx_id, applied FROM retail_charges WHERE id LIKE 'amazon|%'")).fetchall()
    copies: dict[str, list[tuple[int, str, bool]]] = {}
    for id_, tx_id, applied in rows:
        charge, _, n = id_.rpartition('|')
        if n.isdigit():
            copies.setdefault(charge, []).append((int(n), id_, bool(tx_id or applied)))
    gone = []
    for found in copies.values():
        if len(found) >= PAGES:
            kept = math.ceil(len(found) / PAGES)
            gone += [id_ for n, id_, used in found if n > kept and not used]
    for i in range(0, len(gone), 500):
        bind.execute(sa.text("DELETE FROM retail_charges WHERE id IN :ids").bindparams(sa.bindparam('ids', expanding=True)),
                     {'ids': gone[i:i + 500]})


def downgrade() -> None:
    pass
