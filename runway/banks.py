"""Whether any bank is connected: SimpleFIN, or a Plaid bank or card connection. Its own module, so the sync
(server/sync.py) and the notifications (notify.py), which the sync imports, can both ask."""
from __future__ import annotations

from sqlalchemy import select

from . import db, plaid, plaidbank
from . import settings_keys as sk
from .models import PlaidItem


def plaid_banks(conn) -> bool:
    return plaid.configured(conn) and any(plaidbank.is_bank_item(r) for r in conn.execute(select(PlaidItem.products)).fetchall())


def bank_configured(conn) -> bool:
    """Whether there's anything to sync bank accounts from: SimpleFIN, or a Plaid bank or card connection."""
    return bool(db.get_setting(conn, sk.SIMPLEFIN_ACCESS_URL)) or plaid_banks(conn)
