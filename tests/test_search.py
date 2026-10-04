"""Searching transactions finds the text as typed, the same on SQLite and Postgres: % and _ are themselves (not LIKE's
wildcards), and a letter past ASCII matches its capital ("é" finds "CAFÉ") on both."""
from datetime import date

from sqlalchemy import func, insert, select

from runway.models import Account, Transaction
from runway.server.api import transactions
from tests.shared import DbCase

PAYEES = ["50% OFF SALE", "500 ITEMS", "A_B STORE", "AXB STORE", "CAFÉ ROUGE", "café bleu", "STRASSE SHOP", "Straße Laden",
          "Ötzi Outdoor"]


class SearchTests(DbCase):
    def setUp(self):
        super().setUp()
        self.c.execute(insert(Account).values(id="chk", name="Checking", kind="checking", balance=0))
        for i, payee in enumerate(PAYEES):
            self.c.execute(insert(Transaction).values(id=f"chk|{i}", account_id="chk", posted=date(2026, 9, 1 + i).isoformat(),
                                                      amount=-10.0 - i, payee=payee, description="CARD PURCHASE", pending=0))

    def found(self, text: str) -> list[str]:
        out = transactions.api_transactions(self.c, {"q": [text]}, {})
        return sorted(t["payee"] for t in out["items"])

    def test_wildcards_are_themselves(self):
        self.assertEqual(self.found("50%"), ["50% OFF SALE"])
        self.assertEqual(self.found("a_b"), ["A_B STORE"])
        self.assertEqual(self.found("%"), ["50% OFF SALE"])
        self.assertEqual(self.found("_"), ["A_B STORE"])

    def test_letters_past_ascii_match_their_capitals(self):
        for text in ("é", "É", "café", "CAFÉ"):
            with self.subTest(text=text):
                self.assertEqual(self.found(text), ["CAFÉ ROUGE", "café bleu"])
        self.assertEqual(self.found("ötzi"), ["Ötzi Outdoor"])

    def test_sharp_s_is_its_own_letter(self):
        self.assertEqual(self.found("straße"), ["Straße Laden"])
        self.assertEqual(self.found("STRASSE"), ["STRASSE SHOP"])

    def test_lower_is_the_same_on_both(self):
        got = self.c.execute(select(func.lower("ÉCOLE Ärger ÖL"), func.lower(None))).fetchone()
        self.assertEqual(tuple(got), ("école ärger öl", None))
