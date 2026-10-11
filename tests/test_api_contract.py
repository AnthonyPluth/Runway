"""The API contract (runway/server/contract.py, docs/openapi.json, frontend/src/lib/api-types.ts): each covered route's
real reply, on the sample data plus the cases that add fields (a card's statements, a split under a category filter, a
store order), matches docs/openapi.json; the generated files are current; and tools/api_contract.py describes the
types it's given."""
import ast
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import insert, select, update

from runway.domain import demo, splits
from runway.storage.models import (Account, AuthSession, CardStatement, PlaidAccount, PlaidItem, RetailCharge, RetailOrder,
                                   Transaction)
from runway.server.api import accounts, budget, budget_suggest, lock, transactions
from runway.server.common import _current
from tests.shared import TODAY, DbCase, freeze_today
from tests.webauthn_support import ORIGIN, Authenticator

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("api_contract", ROOT / "tools" / "api_contract.py")
assert _spec and _spec.loader
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)

OPENAPI = json.loads((ROOT / "docs/openapi.json").read_text())


def problems(value, schema, at="reply") -> list[str]:
    """What in `value` (parsed JSON) doesn't match `schema` (the JSON schema subset docs/openapi.json uses)."""
    if "$ref" in schema:
        return problems(value, OPENAPI["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]], at)
    if "anyOf" in schema:
        found = [problems(value, s, at) for s in schema["anyOf"]]
        if not all(found):
            return []
        not_null = [f for s, f in zip(schema["anyOf"], found, strict=True) if s != {"type": "null"}]
        if len(not_null) == 1:   # `X | None`, and it isn't None: what's wrong with it as an X
            return not_null[0]
        return [f"{at}: {json.dumps(value)[:80]} is none of the types it can be"]
    if "enum" in schema or "const" in schema:
        allowed = schema["enum"] if "enum" in schema else [schema["const"]]
        return [] if any(value == v and type(value) is type(v) for v in allowed) else [f"{at}: {value!r} isn't one of {allowed}"]
    kinds = schema.get("type")
    if kinds is None:
        return []
    kinds = kinds if isinstance(kinds, list) else [kinds]
    is_a = {"string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool), "boolean": lambda v: isinstance(v, bool),
            "null": lambda v: v is None, "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict)}
    kind = next((k for k in kinds if is_a[k](value)), None)
    if kind is None:
        return [f"{at}: {json.dumps(value)[:80]} isn't {' or '.join(kinds)}"]
    if kind == "array":
        return [p for i, v in enumerate(value) for p in problems(v, schema["items"], f"{at}[{i}]")]
    if kind == "object":
        props = schema.get("properties", {})
        found = [f"{at}: no {k!r}" for k in schema.get("required", []) if k not in value]
        for k, v in value.items():
            if k in props:
                found += problems(v, props[k], f"{at}.{k}")
            elif schema.get("additionalProperties") is False:
                found.append(f"{at}: {k!r} isn't in the contract")
            elif isinstance(schema.get("additionalProperties"), dict):
                found += problems(v, schema["additionalProperties"], f"{at}.{k}")
        return found
    return []


def reply_schema(route: str) -> dict:
    method, path = route.split(" ", 1)
    address = contract.openapi_path(path)[0]
    return OPENAPI["paths"][address][method.lower()]["responses"]["200"]["content"]["application/json"]["schema"]


def covered() -> set[str]:
    return {f"{m.upper()} {'/'.join('{id}' if s.startswith('{') else s for s in p.split('/'))}"
            for p, ops in OPENAPI["paths"].items() for m in ops}


class Replies(DbCase):
    """Each covered route's reply, as the server would send it (through JSON), against docs/openapi.json."""

    def setUp(self):
        super().setUp()
        freeze_today(self)
        demo.seed(self.c, TODAY)
        self.checked: set[str] = set()

    def check(self, route: str, reply) -> None:
        self.checked.add(route)
        found = problems(json.loads(json.dumps(reply, allow_nan=False)), reply_schema(route))
        self.assertEqual(found, [], route)

    def test_every_covered_route_is_checked(self):
        self.test_accounts()
        self.test_budget()
        self.test_transactions()
        self.test_lock()
        self.assertEqual(self.checked, covered(), "check each route the contract covers here")

    def test_accounts(self):
        # A card linked through Plaid with a statement from it; demo-card has one entered by hand, demo-mortgage is a loan.
        self.c.execute(insert(Account).values(id="plaid-card", name="Linked Card", kind="credit"))
        self.c.execute(insert(PlaidItem).values(item_id="it1", access_token="x", institution_name="Card Bank",
                                                products="transactions,liabilities"))
        self.c.execute(insert(PlaidAccount).values(plaid_account_id="pa1", item_id="it1", mask="1234"))
        self.c.execute(insert(CardStatement).values(plaid_account_id="pa1", item_id="it1", last_statement_balance=120.5,
                                                    last_statement_date="2026-09-01", next_due_date="2026-09-25"))
        self.c.execute(update(Account).where(Account.id == "plaid-card").values(plaid_account_id="pa1"))
        out = accounts.api_accounts(self.c, {}, {})
        by_id = {a["id"]: a for a in out}
        self.assertEqual(by_id["plaid-card"]["statement"]["source"], "plaid")
        self.assertEqual(by_id["demo-card"]["statement"]["source"], "manual")
        self.assertIn("loan", by_id["demo-mortgage"])
        self.check("GET /api/accounts", out)

    def test_budget(self):
        self.check("POST /api/budget", budget.api_budget_set(self.c, {}, {"category": "Groceries", "amount": "650"}))
        self.check("POST /api/budget", budget.api_budget_set(self.c, {}, {"category": "Groceries", "rollover": True}))
        for month in (f"{TODAY:%Y-%m}", "2026-01", "2027-03"):   # this month, one that's over, one to come
            self.check("GET /api/budget", budget.api_budget(self.c, {"month": [month]}, {}))
        # Suggestions, with and without a budget now; for next month, and for a month to come
        for month in (f"{TODAY:%Y-%m}", "2027-03"):
            reply = budget_suggest.api_budget_suggestions(self.c, {"month": [month]}, {})
            self.assertTrue(any(s["budget"] is None for s in reply["suggestions"]))
            self.assertTrue(any(s["budget"] is not None for s in reply["suggestions"]))
            self.check("GET /api/budget/suggestions", reply)

    def test_transactions(self):
        made = transactions.api_tx_create(self.c, {}, {"account": "demo-checking", "posted": TODAY.isoformat(),
                                                       "payee": "Corner Shop", "amount": "-42.10", "category": "Groceries"})
        self.check("POST /api/transactions", made)
        # Split across two categories, and a store order it paid for.
        splits.set_splits(self.c, made["id"], [{"category": "Groceries", "amount": -30.10}, {"category": "Shopping", "amount": -12}])
        self.c.execute(insert(RetailOrder).values(id="amazon:111-1", retailer="amazon", order_number="111-1"))
        self.c.execute(insert(RetailCharge).values(id="c1", order_id="amazon:111-1", date=TODAY.isoformat(), amount=-42.10,
                                                   tx_id=made["id"]))
        everything = transactions.api_transactions(self.c, {"limit": ["1000"]}, {})
        self.assertTrue(any(t["retail"] for t in everything["items"]))
        self.check("GET /api/transactions", everything)
        filtered = transactions.api_transactions(self.c, {"category": ["Groceries"]}, {})
        self.assertIn("family", filtered)
        self.assertTrue(any("match" in t for t in filtered["items"]))
        self.check("GET /api/transactions", filtered)
        splits.set_splits(self.c, made["id"], [])
        self.check("DELETE /api/transactions/{id}", transactions.api_tx_delete(self.c, {}, {}, made["id"]))
        self.assertIsNone(self.c.execute(select(Transaction.id).where(Transaction.id == made["id"])).fetchone())

    def test_lock(self):
        # Without sign-in there's none; with it, this session's, turned on with a software passkey, locked and unlocked.
        self.check("GET /api/lock", lock.api_lock(self.c, {}, {}))
        env = {"OIDC_ISSUER": "https://idp.example", "OIDC_CLIENT_ID": "runway", "RUNWAY_PUBLIC_URL": ORIGIN}
        self.c.execute(insert(AuthSession).values(token_hash="h1", sub="u1", email="me@example.com", created=1.0, expires=2e10))
        with mock.patch.dict(os.environ, env), mock.patch.object(_current, "session_key", "h1", create=True):
            a = Authenticator()
            ch = lock.api_lock_challenge(self.c, {}, {"purpose": "register"})
            self.check("POST /api/lock/challenge", ch)
            self.check("POST /api/lock/register", lock.api_lock_register(self.c, {}, a.create(ch["challenge"])))
            self.check("POST /api/lock/settings", lock.api_lock_settings(self.c, {}, {"idle": 300}))
            self.check("POST /api/lock/engage", lock.api_lock_engage(self.c, {}, {}))
            ch = lock.api_lock_challenge(self.c, {}, {"purpose": "unlock"})
            self.check("POST /api/lock/challenge", ch)
            self.check("POST /api/lock/unlock", lock.api_lock_unlock(self.c, {}, a.get(ch["challenge"])))
            self.check("POST /api/lock/key-share", lock.api_lock_key_share(self.c, {}, {}))
            self.check("GET /api/lock", lock.api_lock(self.c, {}, {}))
            self.check("DELETE /api/lock", lock.api_lock_off(self.c, {}, {}))


class Mismatches(unittest.TestCase):
    """The check above notices a reply that isn't the contract's."""

    def test_a_renamed_field_a_wrong_type_and_a_missing_one(self):
        month = {"month": "2026-09", "days_in_month": 30, "day": 23, "categories": [], "income": 0, "income_rows": [],
                 "uncategorized": 0.0, "pay_accounts": [{"id": "a", "name": "A", "kind": None}]}
        schema = reply_schema("GET /api/budget")
        self.assertEqual(problems(month, schema), [])
        renamed = {("month_days" if k == "days_in_month" else k): v for k, v in month.items()}
        self.assertEqual(problems(renamed, schema), ["reply: no 'days_in_month'", "reply: 'month_days' isn't in the contract"])
        self.assertEqual(problems({**month, "day": "23"}, schema), ['reply.day: "23" isn\'t integer'])
        self.assertEqual(problems({**month, "day": True}, schema), ["reply.day: true isn't integer"])
        self.assertEqual(problems({**month, "pay_accounts": [{"id": "a", "name": "A", "kind": 3}]}, schema),
                         ["reply.pay_accounts[0].kind: 3 isn't string or null"])


class Generated(unittest.TestCase):
    def test_the_generated_files_are_current(self):
        doc = contract.build()
        self.assertEqual(contract.OPENAPI_OUT.read_text(), contract.render_json(doc), "run `make api-contract`")
        self.assertEqual(contract.TS_OUT.read_text(), contract.render_ts(doc), "run `make api-contract`")

    def test_only_routes_typed_with_the_contract_s_types_are_covered(self):
        self.assertIn("GET /api/budget", covered())
        self.assertNotIn("GET /api/backup", covered())   # typed, but as a download (common.Response)
        self.assertNotIn("GET /api/categories", covered())

    def describe(self, annotation: str) -> str:
        types = {"Thing": {"doc": None, "fields": []}}
        return contract.ts_type(contract.schema(ast.parse(annotation, mode="eval").body, types, set(), "test"))

    def test_types(self):
        self.assertEqual(self.describe("str | None"), "string | null")
        self.assertEqual(self.describe("int | float"), "number")
        self.assertEqual(self.describe('Literal["a", "b"] | None'), '"a" | "b" | null')
        self.assertEqual(self.describe("list[Thing | None]"), "(Thing | null)[]")
        self.assertEqual(self.describe("dict[str, list[float]]"), "Record<string, number[]>")
        self.assertEqual(self.describe("'Thing'"), "Thing")
        self.assertEqual(self.describe("Any"), "unknown")
        with self.assertRaises(contract.ContractError):
            self.describe("set[str]")

    def test_paths_number_their_ids(self):
        self.assertEqual(contract.openapi_path("/api/things/{id}/parts/{id}/remove"),
                         ("/api/things/{id}/parts/{id2}/remove", ["id", "id2"]))


if __name__ == "__main__":
    unittest.main()

