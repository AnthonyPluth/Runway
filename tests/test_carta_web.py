"""Carta through the browser extension: finding companies and grants in whatever carta.com's pages load."""
import unittest
from datetime import date

from sqlalchemy import func, insert, select

from runway import carta, carta_web, equity
from runway.models import EquityCompany, EquityGrant
from tests.shared import DbCase

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


# The shapes carta.com's portfolio pages actually load (September 2026), trimmed and with made-up names and numbers:
# the securities replies give only the issuer's number, and the company's name is in the list of your companies.
PK = 5550001
WEB = [
    {"url": f"https://app.carta.com/investors/individual/{PK}/portfolio/#embedded-0",
     "data": {"corporation_pk": PK, "account-name": "Pat Doe's Portfolio", "user_first_name": "Pat"}},
    {"url": f"https://app.carta.com/api/investors/portfolio/fund/{PK}/list/",
     "data": {"count": 2, "results": {"companies": [
         {"name": "Northwind Payments, Inc.", "corporation_id": 900, "landing_url": f"/investors/individual/{PK}/portfolio/900/"},
         {"name": "Harbor Sails, Inc.", "corporation_id": 800}]}}},
    {"url": f"https://app.carta.com/api/profiles/profile/{PK}/", "data": {"legal_name": "Pat Doe's Portfolio", "city": "Somewhere"}},
    {"url": f"https://app.carta.com/api/investors/holdings/portfolio/{PK}/corporation/900/securities/",
     "data": {"option_grants": {"holdings": [
         {"id": 1901, "owner_id": PK, "issuer_id": 900, "label": "ES-452", "quantity": 20619, "unit_descriptor": "option",
          "issue_ts_ms": 1732348800000, "expiration_ts_ms": 2047795200000, "is_canceled": False, "is_expired": False,
          "is_vesting": True, "cumulative_vested_shares": 10739, "sub_type": "ISO", "status": "OUTSTANDING",
          "exercised_quantity": 0, "exercise_price": {"currency": "USD", "amount": "3.50"}},
         {"id": 1902, "owner_id": PK, "issuer_id": 900, "label": "ES-858", "quantity": 3002, "issue_ts_ms": 1769155200000,
          "cumulative_vested_shares": 500, "sub_type": "ISO", "status": "OUTSTANDING", "exercised_quantity": 0,
          "exercise_price": {"currency": "USD", "amount": "4.78"}}],
         "aggregations": {"total_quantity": 23621, "vested_quantity": 11239}},
         "rsus": {"holdings": [], "aggregations": {"total_quantity": 0}}}},
    {"url": f"https://app.carta.com/api/issuers/v1/900/fair-market-value/portfolio/{PK}/",
     "data": {"fair_market_value": {"value": {"currency": "USD", "amount": "4.78"}, "expires_at": None}}},
    {"url": f"https://app.carta.com/api/investors/holdings/portfolio/{PK}/corporation/800/securities/",
     "data": {"option_grants": {"holdings": [
         {"id": 1801, "issuer_id": 800, "label": "ES-65", "quantity": 8000, "is_canceled": True, "is_expired": True,
          "status": "CANCELED", "sub_type": "ISO", "cumulative_vested_shares": 3000, "exercised_quantity": 2000,
          "exercise_price": {"currency": "USD", "amount": "0.50"}}]},
      "certificates": {"holdings": [
          {"id": 1802, "issuer_id": 800, "label": "CS-25", "quantity": 2000, "is_canceled": True, "status": "CANCELED",
           "cumulative_vested_shares": 0, "price": {"currency": "USD", "amount": "0.50"}}]}}},
    {"url": f"https://app.carta.com/api/issuers/v1/800/fair-market-value/portfolio/{PK}/",
     "data": {"fair_market_value": {"value": None, "expires_at": None}}},
]


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

    def test_carta_web_app_shapes(self):
        found = carta_web.read(WEB)
        self.assertEqual([(c["id"], c["name"], c["price"]) for c in found["companies"]],
                         [("carta:900", "Northwind Payments, Inc.", 4.78)])   # not the portfolio, nor a company with nothing left
        grants = {g["id"]: (cid, g) for cid, g, _ in found["grants"]}
        self.assertEqual(sorted(grants), ["carta-web:option:1901", "carta-web:option:1902"])   # canceled ones left out
        cid, g = grants["carta-web:option:1901"]
        self.assertEqual((cid, g["kind"], g["label"], g["quantity"], g["strike"], g["vested_reported"], g["exercised"]),
                         ("carta:900", "iso", "ES-452", 20619.0, 3.5, 10739.0, 0.0))
        self.assertEqual((g["granted_on"], g["expires_on"]), ("2024-11-23", "2034-11-22"))
        self.assertEqual(found["gone"], ["carta-web:option:1801", "carta-web:shares:1802"])

    def test_company_known_only_by_number(self):
        found = carta_web.read([WEB[3]])
        self.assertEqual([(c["id"], c["name"]) for c in found["companies"]], [("carta:900", "Carta company 900")])
        self.assertEqual(len(found["grants"]), 2)


class ImportTests(DbCase):
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
        self.assertEqual(self.c.execute(select(func.count()).select_from(EquityGrant)).fetchone()[0], 3)
        self.assertTrue(carta.settings(self.c)["web_last"])

    def test_import_web_app_shapes(self):
        carta_web.start(self.c)
        follow = carta_web.ingest(self.c, WEB[0]["url"], WEB[0]["data"])["follow"]
        self.assertEqual(follow, [f"https://app.carta.com/api/investors/portfolio/fund/{PK}/list/"])
        follow = carta_web.ingest(self.c, WEB[1]["url"], WEB[1]["data"])["follow"]
        self.assertIn(f"https://app.carta.com/api/investors/holdings/portfolio/{PK}/corporation/900/securities/", follow)
        self.assertIn(f"https://app.carta.com/api/issuers/v1/800/fair-market-value/portfolio/{PK}/", follow)
        for c in WEB[2:]:
            carta_web.ingest(self.c, c["url"], c["data"])
        self.assertEqual(carta_web.finish(self.c), {"companies": 1, "grants": 2, "pages": len(WEB)})
        o = equity.overview(self.c, date(2026, 9, 28))
        self.assertEqual([c["name"] for c in o["companies"]], ["Northwind Payments, Inc."])
        # A grant canceled since the last import goes away.
        self.c.execute(insert(EquityGrant).values(id="carta-web:option:1801", company_id="carta:900", kind="iso",
                                                  quantity=8000, source="carta"))
        carta_web.start(self.c)
        for c in WEB:
            carta_web.ingest(self.c, c["url"], c["data"])
        carta_web.finish(self.c)
        self.assertEqual(self.c.execute(select(func.count()).select_from(EquityGrant)).fetchone()[0], 2)

    def test_nothing_found_says_so(self):
        carta_web.start(self.c)
        carta_web.ingest(self.c, "https://app.carta.com/api/me/", {"user": {"name": "Anthony"}})
        self.assertEqual(carta_web.finish(self.c)["grants"], 0)
        self.assertIn("Download what it read", carta.settings(self.c)["web_error"])
        self.assertTrue(carta.settings(self.c)["web_capture"])

    def test_a_later_import_keeps_what_carta_left_out_and_drops_what_is_gone(self):
        from unittest import mock

        def grant(gid, qty, **kw):
            return {"id": gid, "kind": "iso", "label": None, "quantity": qty, "strike": 1.0, "granted_on": "2024-01-10",
                    "vest_start": "2024-01-15", "vest_months": kw.get("months"), "cliff_months": 12, "vest_every": kw.get("every", 1),
                    "exercised": 0.0, "vested_reported": None, "expires_on": None}
        first = {"companies": [{"id": "carta:42", "name": "Acme", "price": 4.25, "price_date": "2026-03-01"}],
                 "grants": [("carta:42", grant("carta:1", 100.0, months=48, every=3), {"a": 1}),
                            ("carta:42", grant("carta:2", 50.0, months=48), {"b": 2})], "gone": []}
        again = {"companies": [{"id": "carta:42", "name": "Carta company 42", "unnamed": True, "price": None, "price_date": None}],
                 "grants": [("carta:42", grant("carta:1", 120.0), {"a": 3})], "gone": ["carta:2"]}
        self.c.execute(insert(EquityGrant).values(id="m1", company_id="carta:42", kind="rsu", quantity=5,
                                                  source="manual"))
        for found in (first, again):
            with mock.patch.object(carta_web, "read", return_value=found):
                carta_web.finish(self.c)
        company = dict(self.c.execute(select(EquityCompany.id, EquityCompany.name, EquityCompany.share_price,
                                             EquityCompany.price_as_of, EquityCompany.source)).fetchone())
        self.assertEqual(company, {"id": "carta:42", "name": "Acme", "share_price": 4.25, "price_as_of": "2026-03-01", "source": "carta"})
        g = EquityGrant
        grants = [tuple(r) for r in self.c.execute(
            select(g.id, g.quantity, g.vest_months, g.vest_every, g.source, g.raw, g.vested_reported_on.is_not(None)).order_by(g.id))]
        self.assertEqual(grants, [("carta:1", 120.0, 48, 3, "carta", '{"a":3}', 1), ("m1", 5.0, None, 1, "manual", None, 0)])


if __name__ == "__main__":
    unittest.main()
