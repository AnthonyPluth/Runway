"""Carta through the browser extension: finding companies and grants in whatever carta.com's pages load."""
import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runway import carta, carta_web, db, equity  # noqa: E402

# Two made-up shapes, since Carta's web app isn't documented: a snake_case one with the company around its
# securities, and a camelCase one with the company inside each security.
HOLDINGS = {
    "portfolio": {"id": 7, "holdings": [{
        "issuer_id": 42, "issuer_name": "Acme Robotics, Inc.", "fair_market_value": {"amount": "4.25"},
        "fmv_date": "2026-03-01", "url": "/api/portfolio/7/issuers/42/securities/",
        "option_grants": [{"id": 11, "label": "ES-7", "quantity": "48,000", "exercise_price": "1.00", "option_type": "ISO",
                           "grant_date": "2024-01-10", "vesting_start_date": "2024-01-15",
                           "vesting_schedule": "1/48 monthly with a 1 year cliff", "vested_quantity": 33000,
                           "expiration_date": "2034-01-10"}],
        "certificates": [{"id": 11, "certificate_label": "CS-9", "quantity": 500, "security_type": "Common", "issue_date": "2023-05-01"}],
    }]},
    "links": ["https://app.carta.com/api/portfolio/7/fmv-history/", "https://app.carta.com/logout/", "https://evil.example.com/x"],
}
OTHER = {"results": [{"id": "r1", "label": "RSU-3", "type": "RSU", "quantity": 1200, "grantDate": "2025-06-01",
                      "vestingSchedule": {"name": "4 years quarterly"}, "vestedQuantity": 300,
                      "company": {"name": "Beta Labs", "id": "b9", "fmvPerShare": 12.5}}]}


class ReadTests(unittest.TestCase):
    def test_finds_companies_and_grants(self):
        found = carta_web.read([{"url": "a", "data": HOLDINGS}, {"url": "b", "data": OTHER}])
        companies = {c["id"]: c for c in found["companies"]}
        self.assertEqual(companies["carta:42"]["name"], "Acme Robotics, Inc.")
        self.assertEqual((companies["carta:42"]["price"], companies["carta:42"]["price_date"]), (4.25, "2026-03-01"))
        self.assertEqual((companies["carta:b9"]["name"], companies["carta:b9"]["price"]), ("Beta Labs", 12.5))
        grants = {g["id"]: (cid, g) for cid, g, _ in found["grants"]}
        cid, og = grants["carta-web:option:11"]
        self.assertEqual((cid, og["kind"], og["quantity"], og["strike"], og["vest_months"], og["cliff_months"], og["vested_reported"]),
                         ("carta:42", "iso", 48000.0, 1.0, 48, 12, 33000.0))
        cid, cs = grants["carta-web:shares:11"]   # same number as the option: kept apart
        self.assertEqual((cid, cs["kind"], cs["quantity"]), ("carta:42", "shares", 500.0))
        cid, rsu = grants["carta-web:rsu:r1"]
        self.assertEqual((cid, rsu["kind"], rsu["vest_months"], rsu["vest_every"]), ("carta:b9", "rsu", 48, 3))


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "c.db")
        db.init(self.path)
        self.c = db.connect(self.path)

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_import(self):
        self.assertEqual(carta_web.start(self.c)["start_url"], "https://app.carta.com/")
        r = carta_web.ingest(self.c, "https://app.carta.com/api/portfolio/7/", HOLDINGS)
        # Carta links worth reading next; never other sites or sign-out
        self.assertEqual(r["follow"], ["https://app.carta.com/api/portfolio/7/fmv-history/",
                                       "https://app.carta.com/api/portfolio/7/issuers/42/securities/"])
        carta_web.ingest(self.c, "https://app.carta.com/api/other/", OTHER)
        with self.assertRaises(carta_web.CartaWebError):
            carta_web.ingest(self.c, "https://evil.example.com/api", OTHER)
        self.assertEqual(carta_web.finish(self.c), {"companies": 2, "grants": 3, "pages": 2})
        o = equity.overview(self.c, date(2026, 9, 27))
        acme = next(c for c in o["companies"] if c["name"].startswith("Acme"))
        self.assertEqual((acme["source"], acme["share_price"]), ("carta", 4.25))
        self.assertEqual(acme["vested_value"], round(33000 * 3.25 + 500 * 4.25, 2))
        # Importing again updates rather than duplicates, and a new import starts afresh.
        carta_web.start(self.c)
        carta_web.ingest(self.c, "https://app.carta.com/api/portfolio/7/", HOLDINGS)
        carta_web.finish(self.c)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM equity_grants").fetchone()[0], 3)
        self.assertTrue(carta.settings(self.c)["web_last"])

    def test_nothing_found_says_so(self):
        carta_web.start(self.c)
        carta_web.ingest(self.c, "https://app.carta.com/api/me/", {"user": {"name": "Anthony"}})
        self.assertEqual(carta_web.finish(self.c)["grants"], 0)
        self.assertIn("Download what it read", carta.settings(self.c)["web_error"])
        self.assertTrue(carta.settings(self.c)["web_capture"])


if __name__ == "__main__":
    unittest.main()
