"""The browser extension's key no longer expires with age: it used to stop working 90 days after it was made. A key
already past its 90 days when Runway is updated had stopped working, and someone who shouldn't may have it, so it doesn't
come back: it's removed here (its hash, when it was made, who made it and when it was last used), as Settings →
Connections' Remove does, and the extension's calls get "Runway doesn't know this key" until a new one is made. A key
still inside its 90 days carries on, and from now on doesn't expire. One whose date can't be read never expired, and is
left as it is. A backup from before this is brought up to date the same way when it's restored.

Going back down changes nothing: a removed key's hash is gone, and the older version expires the keys left by their dates.

Revision ID: 0048
Revises: 0047
"""
from datetime import datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = '0048'
down_revision = '0047'
branch_labels = None
depends_on = None

EXPIRED_AFTER = timedelta(days=90)   # how long a key worked before this (the old retail.TOKEN_DAYS)
KEYS = ('retail_token_hash', 'retail_token_created', 'retail_token_owner', 'retail_token_used')


def _made(text):
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def upgrade() -> None:
    bind = op.get_bind()
    made = _made(bind.execute(sa.text("SELECT value FROM settings WHERE key = 'retail_token_created'")).scalar())
    # Local time with no zone, as the key's date is written (retail.new_token) and as the older version compared it.
    if made and datetime.now() - made >= EXPIRED_AFTER:
        bind.execute(sa.text("DELETE FROM settings WHERE key IN :keys").bindparams(sa.bindparam('keys', expanding=True)),
                     {'keys': list(KEYS)})


def downgrade() -> None:
    pass
