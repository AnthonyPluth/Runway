"""ORM models: one class per table in runway/schema.py.

schema.py stays the one source of truth for the schema (Alembic compares the database with it): each model maps
onto its `Table` (`__table__ = schema.x`), so there's nothing here to migrate. The `Mapped[...]` annotations are for
readers and type checkers; they must match schema.py (tests/test_models.py checks).

Use the classes' attributes in SQLAlchemy statements (`select(Account.id, Account.name).where(Account.hidden == 0)`,
run with `conn.execute(...)`), or load objects through the connection's ORM session (`conn.orm.get(Asset, 3)`).
docs/orm.md has the conventions.

Relationships: the schema has no foreign keys, so each one spells out its join. They're all `viewonly` (writes go
through the columns, as before: deleting an order doesn't quietly touch its items) and `lazy="raise"`, so reading one
that wasn't loaded up front fails loudly rather than running a query per row: load them with
`options(selectinload(RetailOrder.items))`, or join on them (`select(...).join(Account.transactions)`).
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase, Mapped, relationship

from . import schema


class Base(DeclarativeBase):
    metadata = schema.metadata


def _rel(target: str, join: str, **kw):
    """A read-only relationship that must be loaded explicitly (see above)."""
    return relationship(target, primaryjoin=join, viewonly=True, lazy="raise", **kw)


class Account(Base):
    __table__ = schema.accounts
    id: Mapped[str]
    name: Mapped[str]
    display_name: Mapped[str | None]
    org: Mapped[str | None]
    currency: Mapped[str | None]
    balance: Mapped[float | None]
    available: Mapped[float | None]
    balance_date: Mapped[str | None]
    kind: Mapped[str | None]
    pay_from: Mapped[str | None]
    owed_positive: Mapped[int | None]
    in_forecast: Mapped[int | None]
    daily_spend: Mapped[int | None]
    hidden: Mapped[int | None]
    owner: Mapped[str | None]
    provider: Mapped[str | None]
    plaid_account_id: Mapped[str | None]
    provider_since: Mapped[str | None]

    transactions: Mapped[list[Transaction]] = _rel("Transaction", "foreign(Transaction.account_id) == Account.id")


class Transaction(Base):
    __table__ = schema.transactions
    id: Mapped[str]
    account_id: Mapped[str]
    posted: Mapped[str]
    amount: Mapped[float]
    description: Mapped[str | None]
    payee: Mapped[str | None]
    category: Mapped[str | None]
    category_source: Mapped[str | None]
    confidence: Mapped[float | None]
    needs_review: Mapped[int | None]
    pending: Mapped[int | None]
    created_at: Mapped[str | None]
    recurring_id: Mapped[int | None]
    is_split: Mapped[int | None]
    merchant_id: Mapped[str | None]

    account: Mapped[Account] = _rel("Account", "foreign(Transaction.account_id) == Account.id")
    splits: Mapped[list[TxSplit]] = _rel("TxSplit", "foreign(TxSplit.tx_id) == Transaction.id",
                                         order_by="(TxSplit.position, TxSplit.id)")


class Merchant(Base):
    __table__ = schema.merchants
    id: Mapped[str]
    name: Mapped[str | None]
    website: Mapped[str | None]
    logo_url: Mapped[str | None]
    logo: Mapped[str | None]
    logo_type: Mapped[str | None]
    logo_checked: Mapped[str | None]


class MerchantLogo(Base):
    __table__ = schema.merchant_logos
    key: Mapped[str]
    website: Mapped[str | None]
    hidden: Mapped[int | None]


class TxSplit(Base):
    __table__ = schema.tx_splits
    id: Mapped[int]
    tx_id: Mapped[str]
    amount: Mapped[float]
    category: Mapped[str | None]
    note: Mapped[str | None]
    position: Mapped[int | None]


class RetailOrder(Base):
    __table__ = schema.retail_orders
    id: Mapped[str]
    retailer: Mapped[str]
    order_number: Mapped[str]
    channel: Mapped[str | None]
    placed: Mapped[str | None]
    total: Mapped[float | None]
    subtotal: Mapped[float | None]
    tax: Mapped[float | None]
    shipping: Mapped[float | None]
    payment: Mapped[str | None]
    details: Mapped[int | None]
    attempts: Mapped[int | None]
    raw: Mapped[str | None]
    updated: Mapped[str | None]

    items: Mapped[list[RetailItem]] = _rel("RetailItem", "foreign(RetailItem.order_id) == RetailOrder.id",
                                           order_by="(RetailItem.position, RetailItem.id)")
    charges: Mapped[list[RetailCharge]] = _rel("RetailCharge", "foreign(RetailCharge.order_id) == RetailOrder.id",
                                               order_by="(RetailCharge.date, RetailCharge.id)")


class RetailItem(Base):
    __table__ = schema.retail_items
    id: Mapped[int]
    order_id: Mapped[str]
    position: Mapped[int | None]
    title: Mapped[str | None]
    quantity: Mapped[float | None]
    amount: Mapped[float | None]
    department: Mapped[str | None]
    category: Mapped[str | None]
    category_source: Mapped[str | None]
    confidence: Mapped[float | None]


class RetailCharge(Base):
    __table__ = schema.retail_charges
    id: Mapped[str]
    order_id: Mapped[str]
    date: Mapped[str]
    amount: Mapped[float]
    payment: Mapped[str | None]
    tx_id: Mapped[str | None]
    match_source: Mapped[str | None]
    not_tx: Mapped[str | None]
    applied: Mapped[str | None]


class RetailItemMemory(Base):
    __table__ = schema.retail_item_memory
    key: Mapped[str]
    category: Mapped[str]


class EquityCompany(Base):
    __table__ = schema.equity_companies
    id: Mapped[str]
    name: Mapped[str]
    share_price: Mapped[float | None]
    price_as_of: Mapped[str | None]
    in_networth: Mapped[int | None]
    source: Mapped[str | None]
    raw: Mapped[str | None]
    updated: Mapped[str | None]

    grants: Mapped[list[EquityGrant]] = _rel("EquityGrant", "foreign(EquityGrant.company_id) == EquityCompany.id")


class EquityGrant(Base):
    __table__ = schema.equity_grants
    id: Mapped[str]
    company_id: Mapped[str]
    kind: Mapped[str]
    label: Mapped[str | None]
    granted_on: Mapped[str | None]
    quantity: Mapped[float]
    strike: Mapped[float | None]
    vest_start: Mapped[str | None]
    vest_months: Mapped[int | None]
    cliff_months: Mapped[int | None]
    vest_every: Mapped[int | None]
    exercised: Mapped[float | None]
    vested_reported: Mapped[float | None]
    vested_reported_on: Mapped[str | None]
    expires_on: Mapped[str | None]
    source: Mapped[str | None]
    raw: Mapped[str | None]

    company: Mapped[EquityCompany] = _rel("EquityCompany", "foreign(EquityGrant.company_id) == EquityCompany.id")


class Category(Base):
    __table__ = schema.categories
    name: Mapped[str]
    is_transfer: Mapped[int | None]
    is_income: Mapped[int | None]
    parent: Mapped[str | None]
    icon: Mapped[str | None]
    color: Mapped[str | None]


class Rule(Base):
    __table__ = schema.rules
    id: Mapped[int]
    match: Mapped[str]
    category: Mapped[str | None]
    created_at: Mapped[str | None]
    match_mode: Mapped[str | None]
    amount_min: Mapped[float | None]
    amount_max: Mapped[float | None]
    direction: Mapped[str | None]
    account_id: Mapped[str | None]
    rename: Mapped[str | None]
    review: Mapped[int | None]
    split: Mapped[str | None]


class Recurring(Base):
    __table__ = schema.recurring
    id: Mapped[int]
    name: Mapped[str]
    account_id: Mapped[str]
    amount: Mapped[float]
    frequency: Mapped[str]
    anchor_date: Mapped[str]
    match: Mapped[str | None]
    end_date: Mapped[str | None]
    active: Mapped[int | None]
    amount_mode: Mapped[str | None]
    dates: Mapped[str | None]


class RecurringDismissed(Base):
    __table__ = schema.recurring_dismissed
    key: Mapped[str]


class Budget(Base):
    __table__ = schema.budgets
    category: Mapped[str]
    amount: Mapped[float]
    pay_with: Mapped[str | None]
    rollover_from: Mapped[str | None]


class Override(Base):
    __table__ = schema.overrides
    key: Mapped[str]
    amount: Mapped[float]


class PlaidItem(Base):
    __table__ = schema.plaid_items
    item_id: Mapped[str]
    access_token: Mapped[str]
    institution_id: Mapped[str | None]
    institution_name: Mapped[str | None]
    env: Mapped[str | None]
    created_at: Mapped[str | None]
    last_sync: Mapped[str | None]
    error: Mapped[str | None]
    products: Mapped[str | None]
    cursor: Mapped[str | None]

    accounts: Mapped[list[PlaidAccount]] = _rel("PlaidAccount", "foreign(PlaidAccount.item_id) == PlaidItem.item_id")


class PlaidAccount(Base):
    __table__ = schema.plaid_accounts
    plaid_account_id: Mapped[str]
    item_id: Mapped[str]
    name: Mapped[str | None]
    official_name: Mapped[str | None]
    mask: Mapped[str | None]
    type: Mapped[str | None]
    subtype: Mapped[str | None]
    current: Mapped[float | None]
    available: Mapped[float | None]
    ignored: Mapped[int | None]

    item: Mapped[PlaidItem] = _rel("PlaidItem", "foreign(PlaidAccount.item_id) == PlaidItem.item_id")


class CardStatement(Base):
    __table__ = schema.card_statements
    plaid_account_id: Mapped[str]
    item_id: Mapped[str]
    last_statement_balance: Mapped[float | None]
    last_statement_date: Mapped[str | None]
    next_due_date: Mapped[str | None]
    minimum_payment: Mapped[float | None]
    last_payment_amount: Mapped[float | None]
    last_payment_date: Mapped[str | None]
    is_overdue: Mapped[int | None]
    updated: Mapped[str | None]


class InvAccount(Base):
    __table__ = schema.inv_accounts
    id: Mapped[str]
    item_id: Mapped[str]
    name: Mapped[str | None]
    official_name: Mapped[str | None]
    type: Mapped[str | None]
    subtype: Mapped[str | None]
    mask: Mapped[str | None]
    balance: Mapped[float | None]
    currency: Mapped[str | None]
    hidden: Mapped[int | None]
    source: Mapped[str | None]
    institution: Mapped[str | None]
    account_id: Mapped[str | None]

    holdings: Mapped[list[Holding]] = _rel("Holding", "foreign(Holding.account_id) == InvAccount.id")


class Security(Base):
    __table__ = schema.securities
    id: Mapped[str]
    ticker: Mapped[str | None]
    name: Mapped[str | None]
    type: Mapped[str | None]
    subtype: Mapped[str | None]
    close_price: Mapped[float | None]
    close_as_of: Mapped[str | None]
    is_cash: Mapped[int | None]
    sector: Mapped[str | None]
    industry: Mapped[str | None]
    currency: Mapped[str | None]
    cusip: Mapped[str | None]
    isin: Mapped[str | None]


class Holding(Base):
    __table__ = schema.holdings
    account_id: Mapped[str]
    security_id: Mapped[str]
    quantity: Mapped[float | None]
    price: Mapped[float | None]
    price_as_of: Mapped[str | None]
    value: Mapped[float | None]
    cost_basis: Mapped[float | None]
    currency: Mapped[str | None]

    security: Mapped[Security] = _rel("Security", "foreign(Holding.security_id) == Security.id")


class InvTransaction(Base):
    __table__ = schema.inv_transactions
    id: Mapped[str]
    account_id: Mapped[str]
    security_id: Mapped[str | None]
    date: Mapped[str]
    name: Mapped[str | None]
    type: Mapped[str | None]
    subtype: Mapped[str | None]
    quantity: Mapped[float | None]
    amount: Mapped[float | None]
    price: Mapped[float | None]
    fees: Mapped[float | None]
    currency: Mapped[str | None]


class InvSnapshot(Base):
    __table__ = schema.inv_snapshots
    date: Mapped[str]
    account_id: Mapped[str]
    value: Mapped[float | None]


class AuthPending(Base):
    __table__ = schema.auth_pending
    state: Mapped[str]
    nonce: Mapped[str | None]
    verifier: Mapped[str | None]
    next: Mapped[str | None]
    created: Mapped[float | None]


class AuthSession(Base):
    __table__ = schema.auth_sessions
    token_hash: Mapped[str]
    sub: Mapped[str | None]
    email: Mapped[str | None]
    name: Mapped[str | None]
    created: Mapped[float | None]
    expires: Mapped[float | None]
    id_token: Mapped[str | None]


class PushSubscription(Base):
    __table__ = schema.push_subscriptions
    endpoint: Mapped[str]
    p256dh: Mapped[str]
    auth: Mapped[str]
    device: Mapped[str | None]
    user_sub: Mapped[str | None]
    created: Mapped[float | None]
    last_ok: Mapped[float | None]
    last_error: Mapped[str | None]


class NotifyLog(Base):
    __table__ = schema.notify_log
    key: Mapped[str]
    sent: Mapped[float | None]
    title: Mapped[str | None]


class User(Base):
    __table__ = schema.users
    sub: Mapped[str]
    email: Mapped[str | None]
    name: Mapped[str | None]
    first_name: Mapped[str | None]
    last_seen: Mapped[float | None]


class AiLog(Base):
    __table__ = schema.ai_log
    id: Mapped[int]
    at: Mapped[str | None]
    purpose: Mapped[str | None]
    model: Mapped[str | None]
    merchants: Mapped[int | None]
    answered: Mapped[int | None]
    new_cats: Mapped[int | None]
    ok: Mapped[int | None]
    seconds: Mapped[float | None]
    message: Mapped[str | None]
    reply: Mapped[str | None]


class Asset(Base):
    __table__ = schema.assets
    id: Mapped[int]
    name: Mapped[str]
    kind: Mapped[str]
    value: Mapped[float | None]
    as_of: Mapped[str | None]
    source: Mapped[str | None]
    yearly_change: Mapped[float | None]
    address: Mapped[str | None]
    url: Mapped[str | None]
    loan_account_id: Mapped[str | None]
    auto_update: Mapped[int | None]
    low: Mapped[float | None]
    high: Mapped[float | None]
    last_lookup: Mapped[str | None]
    notes: Mapped[str | None]
    created_at: Mapped[str | None]

    history: Mapped[list[AssetValue]] = _rel("AssetValue", "foreign(AssetValue.asset_id) == Asset.id",
                                             order_by="AssetValue.date")


class AssetValue(Base):
    __table__ = schema.asset_values
    asset_id: Mapped[int]
    date: Mapped[str]
    value: Mapped[float | None]
    source: Mapped[str | None]


class NetworthSnapshot(Base):
    __table__ = schema.networth_snapshots
    date: Mapped[str]
    assets: Mapped[float | None]
    liabilities: Mapped[float | None]
    net: Mapped[float | None]
    detail: Mapped[str | None]


class ManualPosition(Base):
    __table__ = schema.manual_positions
    account_id: Mapped[str]
    security_id: Mapped[str]
    shares: Mapped[float | None]
    pct: Mapped[float | None]
    last_value: Mapped[float | None]
    updated: Mapped[str | None]


class ManualContribution(Base):
    __table__ = schema.manual_contributions
    # The table has no primary key; the ORM needs one to tell rows apart, so all three columns stand in.
    __mapper_args__ = {"primary_key": [schema.manual_contributions.c.account_id, schema.manual_contributions.c.date,
                                         schema.manual_contributions.c.amount]}
    account_id: Mapped[str | None]
    date: Mapped[str | None]
    amount: Mapped[float | None]


class ManualState(Base):
    __table__ = schema.manual_state
    account_id: Mapped[str]
    drift: Mapped[float | None]
    checked: Mapped[str | None]
    last_balance: Mapped[float | None]
    baseline: Mapped[float | None]


class CostOverride(Base):
    __table__ = schema.cost_overrides
    account_id: Mapped[str]
    security_id: Mapped[str]
    cost_basis: Mapped[float]
    per_share: Mapped[float | None]


class HoldingSnapshot(Base):
    __table__ = schema.holding_snapshots
    date: Mapped[str]
    account_id: Mapped[str]
    security_id: Mapped[str]
    quantity: Mapped[float | None]
    value: Mapped[float | None]


class Price(Base):
    __table__ = schema.prices
    ticker: Mapped[str]
    date: Mapped[str]
    close: Mapped[float | None]
    adjclose: Mapped[float | None]


class PriceMeta(Base):
    __table__ = schema.price_meta
    ticker: Mapped[str]
    fetched_at: Mapped[str | None]
    ok: Mapped[int | None]
    splits: Mapped[str | None]
    instrument_type: Mapped[str | None]
    long_name: Mapped[str | None]


class ChurnCard(Base):
    __table__ = schema.churn_cards
    id: Mapped[int]
    owner: Mapped[str]
    issuer: Mapped[str]
    product: Mapped[str]
    family: Mapped[str | None]
    account_id: Mapped[str | None]
    opened_on: Mapped[str]
    closed_on: Mapped[str | None]
    status: Mapped[str | None]
    changed_from: Mapped[int | None]
    authorized_user: Mapped[int | None]
    business: Mapped[int | None]
    annual_fee: Mapped[float | None]
    fee_month: Mapped[int | None]
    currency: Mapped[str | None]
    base_rate: Mapped[float | None]
    earn_note: Mapped[str | None]
    bonus: Mapped[float | None]
    bonus_spend: Mapped[float | None]
    bonus_months: Mapped[int | None]
    bonus_deadline: Mapped[str | None]
    bonus_earned_on: Mapped[str | None]
    manual_spend: Mapped[float | None]
    eligible_on: Mapped[str | None]
    notes: Mapped[str | None]
    created_at: Mapped[str | None]


class ChurnRate(Base):
    __table__ = schema.churn_rates
    card_id: Mapped[int]
    category: Mapped[str]
    multiplier: Mapped[float]


class ChurnCurrency(Base):
    __table__ = schema.churn_currencies
    key: Mapped[str]
    name: Mapped[str]
    cents: Mapped[float]


class ChurnBalance(Base):
    __table__ = schema.churn_balances
    owner: Mapped[str]
    currency: Mapped[str]
    points: Mapped[float]
    as_of: Mapped[str | None]


class ChurnTask(Base):
    __table__ = schema.churn_tasks
    id: Mapped[int]
    card_id: Mapped[int]
    due_on: Mapped[str]
    action: Mapped[str]
    done: Mapped[int | None]


class ChurnBankBonus(Base):
    __table__ = schema.churn_bank_bonuses
    id: Mapped[int]
    owner: Mapped[str]
    bank: Mapped[str]
    account_type: Mapped[str | None]
    account_id: Mapped[str | None]
    opened_on: Mapped[str]
    bonus: Mapped[float]
    dd_total: Mapped[float | None]
    dd_count: Mapped[int | None]
    debit_count: Mapped[int | None]
    min_balance: Mapped[float | None]
    hold_until: Mapped[str | None]
    other_reqs: Mapped[str | None]
    deadline_days: Mapped[int | None]
    deadline: Mapped[str | None]
    post_days: Mapped[int | None]
    manual_dd: Mapped[float | None]
    manual_debits: Mapped[int | None]
    status: Mapped[str | None]
    received_on: Mapped[str | None]
    received_amount: Mapped[float | None]
    closed_on: Mapped[str | None]
    monthly_fee: Mapped[float | None]
    fee_waiver: Mapped[str | None]
    early_close_fee: Mapped[float | None]
    keep_open_days: Mapped[int | None]
    repeat_months: Mapped[int | None]
    once_per_lifetime: Mapped[int | None]
    eligible_on: Mapped[str | None]
    notes: Mapped[str | None]
    created_at: Mapped[str | None]


class Setting(Base):
    __table__ = schema.settings
    key: Mapped[str]
    value: Mapped[str | None]


class SyncLog(Base):
    __table__ = schema.sync_log
    id: Mapped[int]
    at: Mapped[str | None]
    ok: Mapped[int | None]
    message: Mapped[str | None]
