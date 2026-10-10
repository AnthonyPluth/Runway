"""The API's contract with the web app: the request bodies and replies of the routes it covers, as TypedDicts.

A handler names its reply (its return annotation) and, for a route that takes one, its body (the annotation of its third
parameter) with a type from here; mypy then holds the handler to it, and tools/api_contract.py turns those annotations,
with the route table (routes.py), into docs/openapi.json and the web app's frontend/src/lib/api-types.ts. `make check`
and CI fail when either is out of date, and tests/test_api_contract.py checks each covered route's real reply against
docs/openapi.json, so a field renamed on one side fails the web app's type-check, the drift check or the tests.

A body is what the web app sends. The handler still checks every value it reads (runway/validate.py): anyone can send
anything, so a field typed `float | str` here is one the validators accept as either ("12.50" or 12.5).

Only the types the generator understands are used here: str, int, float, bool, None, Any, `X | Y`, list[X],
dict[str, X], Literal[...], NotRequired[X], and the TypedDicts in this module (a subclass has its base's fields too).
Not every route is covered yet; tools/api_contract.py lists the ones that are.
"""
from __future__ import annotations

from typing import Literal, NotRequired, TypedDict


class Ok(TypedDict):
    ok: bool


# Accounts

class AccountColumns(TypedDict):
    """A row of the accounts table (models.Account), as `select(Account)` gives it."""
    id: str
    name: str
    display_name: str | None
    org: str | None
    currency: str | None
    balance: float | None
    available: float | None
    balance_date: str | None
    kind: str | None
    pay_from: str | None
    owed_positive: int | None
    in_forecast: int | None
    hidden: int | None
    networth_hidden: int | None
    owner: str | None
    provider: str | None
    plaid_account_id: str | None
    provider_since: str | None
    logo: str | None
    interest_rate: float | None
    monthly_payment: float | None


class PlaidLink(TypedDict):
    """The Plaid connection an account is matched to: its bank, the account's last digits, whether it brings
    transactions, its latest statement's close and due dates, and the note Plaid's statements carry."""
    institution: str | None
    mask: str | None
    transactions: bool
    closed: str | None
    due: str | None
    statement_note: str | None


class PlaidStatement(TypedDict):
    """A card's statement from Plaid (the forecast uses it over one you entered)."""
    source: Literal["plaid"]
    institution: str | None
    closed: str
    due: str | None
    balance: float | None
    minimum: float | None


class EnteredStatement(TypedDict):
    """The latest card statement you entered, whether a newer one should have been by now, and when the next closes."""
    source: Literal["manual"]
    closed: str
    due: str | None
    balance: float | None
    minimum: float | None
    stale: bool
    next_close: str


class ManualStatement(TypedDict):
    """A card statement you entered (statements.history_of)."""
    statement_date: str
    balance: float
    due_date: str
    minimum_payment: float | None
    entered_at: str | None


class LoanTerms(TypedDict):
    """A loan's terms (loans.terms): `rate` (annual %) and `payment` are what's used, and where they came from."""
    rate: float | None
    payment: float | None
    maturity: str | None
    source: Literal["plaid", "manual", "inferred"] | None
    plaid: bool
    plaid_payment: bool
    set_rate: float | None
    set_payment: float | None
    inferred_payment: float | None


class AccountItem(AccountColumns):
    """A row of GET /api/accounts: the account, its Plaid link, and (cards) its statements and how it's paid, (loans)
    its terms."""
    plaid_link: PlaidLink | None
    statement: NotRequired[PlaidStatement | EnteredStatement | None]
    statements: NotRequired[list[ManualStatement]]
    pay_mode: NotRequired[Literal["full", "minimum", "fixed"]]
    pay_amount: NotRequired[float | None]
    apr: NotRequired[float | None]
    issuer_apr: NotRequired[float | None]
    loan: NotRequired[LoanTerms]


# Budget

class BudgetCategory(TypedDict):
    """A category's month on the Budget page: what's been spent (its subcategories included) against its budget, if it
    has one. An income category's row has the same shape: `budget` is what's expected in, `spent` what has come.
    `budget` is the month's: its own amount (`month_budget`, set for that month only) when it has one, else the
    budget's usual amount (`usual_budget`, every other month's)."""
    name: str
    parent: str | None
    path: list[str]
    depth: int
    top: str
    has_children: bool
    budget: float | None
    usual_budget: float | None
    month_budget: float | None
    pay_with: str | None
    rollover_from: str | None
    carried: float
    available: float | None
    usual_account: str | None
    spent: float
    own_spent: float
    left: float | None
    expected: float


class PayAccount(TypedDict):
    id: str
    name: str
    kind: str | None


class BudgetMonth(TypedDict):
    """GET /api/budget?month=YYYY-MM."""
    month: str
    days_in_month: int
    day: int
    categories: list[BudgetCategory]
    income: float
    income_rows: list[BudgetCategory]
    uncategorized: float
    pay_accounts: list[PayAccount]


class BudgetSet(TypedDict):
    """POST /api/budget: a category's budget (an empty or zero amount removes it), or whether it rolls over, or (kept
    for a release) the account it's paid with. With `month` ("YYYY-MM"), the budget's own amount for that month only
    (it needs a budget already): an empty amount takes the month back to the usual amount, 0 budgets nothing then."""
    category: str
    amount: NotRequired[float | str | None]
    month: NotRequired[str]
    rollover: NotRequired[bool]
    pay_with: NotRequired[str | None]


class RaisedBudget(TypedDict):
    category: str
    amount: float


class BudgetSaved(TypedDict):
    """POST /api/budget's reply: the parents' budgets raised to cover their subcategories' (after an amount was saved)."""
    ok: bool
    raised: NotRequired[list[RaisedBudget]]


class BudgetSuggestion(TypedDict):
    """A suggested monthly budget for a category (domain/budget_suggest.py): the larger of its typical month and the
    recurring payments coming in the budget's month, rounded up. `budget` is the category's budget now, if it has one."""
    category: str
    suggested: float
    typical: float
    recurring: float
    budget: float | None


class BudgetSuggestions(TypedDict):
    """GET /api/budget/suggestions?month=YYYY-MM: the suggestions, and what they're from: the full months of history
    looked at (`months`, from `first` to `last`) and the month whose recurring payments count (`recurring_month`: the
    month asked for when it's still to come, else next month; never past the forecast's reach)."""
    months: int
    first: str | None
    last: str | None
    recurring_month: str
    suggestions: list[BudgetSuggestion]


# Transactions

class TransactionColumns(TypedDict):
    """A row of the transactions table (models.Transaction), as `select(Transaction)` gives it."""
    id: str
    account_id: str
    posted: str
    amount: float
    description: str | None
    payee: str | None
    category: str | None
    category_source: str | None
    confidence: float | None
    needs_review: int | None
    pending: int | None
    created_at: str | None
    recurring_id: int | None
    is_split: int | None
    merchant_id: str | None
    recurring_linked_by: Literal["you", "auto"] | None
    notes: str | None
    bank_posted: str | None
    bank_amount: float | None


class Split(TypedDict):
    id: int
    amount: float
    category: str | None
    note: str | None


class SplitMatch(TypedDict):
    """Under a category filter, a split transaction's parts in that category (or its subcategories)."""
    amount: float
    categories: list[str]


class OrderSummary(TypedDict):
    """The store order a charge paid for (or a refund came from)."""
    order_id: str
    charge_id: str
    retailer: str
    order_number: str
    channel: str | None
    items: int


class BrandChoice(TypedDict):
    brand: str
    bank_name: str
    using: Literal["brand", "bank"]


class Tx(TransactionColumns):
    """A row of GET /api/transactions."""
    account_name: str | None
    account_kind: str | None
    recurring_name: str | None
    splits: list[Split]
    match: NotRequired[SplitMatch]
    retail: OrderSummary | None
    logo: str | None
    logo_account: str | None
    brand: BrandChoice | None
    source: Literal["manual", "plaid", "simplefin"]


class TxList(TypedDict):
    """GET /api/transactions: a page of them, how many match in all, what they add up to, and (under a category filter)
    the category and its subcategories."""
    items: list[Tx]
    total: int
    sum: float
    family: NotRequired[list[str]]


class TxNew(TypedDict):
    """POST /api/transactions: a transaction added by hand. `amount` is positive for money in."""
    account: str
    posted: str
    payee: str
    amount: float | str
    category: NotRequired[str | None]
    notes: NotRequired[str | None]


class TxCreated(TypedDict):
    ok: bool
    id: str


# App lock (runway/applock.py)

class LockStatus(TypedDict):
    """This signed-in device's app lock: whether it can have one (sign-in is on), whether it's on, whether it's locked
    now, how long away locks it (seconds: 0, 60, 300 or 900), and its passkey's id (base64url)."""
    available: bool
    on: bool
    locked: bool
    idle: int
    credential_id: str | None


class LockChallengeAsk(TypedDict):
    """POST /api/lock/challenge: a challenge for turning the lock on (register) or unlocking."""
    purpose: Literal["register", "unlock"]


class LockChallenge(TypedDict):
    """A single-use challenge (base64url) for this session, the relying party's ID (Runway's host), and for an unlock,
    the passkey to use."""
    challenge: str
    rp_id: str
    credential_id: str | None


class LockRegister(TypedDict):
    """POST /api/lock/register: the passkey navigator.credentials.create made, all base64url, and when to lock."""
    credential_id: str
    client_data: str
    authenticator_data: str
    public_key: str
    alg: int
    idle: int


class LockUnlock(TypedDict):
    """POST /api/lock/unlock: navigator.credentials.get's answer to the unlock challenge, all base64url."""
    credential_id: str
    client_data: str
    authenticator_data: str
    signature: str


class LockIdle(TypedDict):
    """POST /api/lock/settings: how long away locks it (seconds: 0, 60, 300 or 900)."""
    idle: int

