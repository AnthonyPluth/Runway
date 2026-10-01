"""Equity compensation: vesting, what grants are worth, net worth, and reading it from Carta."""
import io
import json
import time
import unittest
import urllib.error
import urllib.parse
from datetime import date

from sqlalchemy import func, select, update

from runway import carta, db, equity, networth
from runway.models import EquityGrant, Setting
from tests.shared import DbCase

TODAY = date(2026, 9, 27)


def grant(**kw):
    g = {"kind": "iso", "quantity": 4800, "strike": 1.0, "vest_start": "2024-01-15", "vest_months": 48, "cliff_months": 12,
         "vest_every": 1, "exercised": 0, "vested_reported": None, "granted_on": "2024-01-10"}
    g.update(kw)
    return g


class VestingTests(unittest.TestCase):
    def test_cliff_then_monthly(self):
        g = grant()
        self.assertEqual(equity.vested_on(g, date(2025, 1, 14)), 0)          # a day before the cliff
        self.assertEqual(equity.vested_on(g, date(2025, 1, 15)), 1200)       # a year at once
        self.assertEqual(equity.vested_on(g, date(2025, 2, 14)), 1200)
        self.assertEqual(equity.vested_on(g, date(2025, 2, 15)), 1300)
        self.assertEqual(equity.vested_on(g, date(2030, 1, 1)), 4800)
        self.assertEqual(equity.fully_vested_on(g), "2028-01-15")

    def test_quarterly_and_immediate(self):
        q = grant(cliff_months=None, vest_every=3, vest_months=12, quantity=1200)
        self.assertEqual([equity.vested_on(q, date(2024, m, 20)) for m in (3, 4, 7)], [0, 300, 600])
        self.assertEqual(equity.vested_on(grant(kind="shares", vest_months=None), TODAY), 4800)
        self.assertEqual(equity.vested_on(grant(vest_start="2027-01-01", vest_months=None), TODAY), 0)
        self.assertEqual(equity.schedule(q)[-1], ("2025-01-15", 1200.0))

    def test_values(self):
        g = grant(exercised=500)
        v = equity.value(g, 5.0, 2000)   # 2,000 vested, 500 of them exercised
        self.assertEqual(v["vested_value"], 4 * 1500 + 5 * 500)
        self.assertEqual(v["unvested_value"], 4 * 2800)
        self.assertEqual(v["exercise_cost"], 1500.0)
        self.assertEqual(equity.value(g, 0.5, 2000)["vested_value"], 250.0)   # under water: only the exercised shares
        self.assertEqual(equity.value(grant(kind="rsu", strike=None), 5.0, 100)["vested_value"], 500.0)

    def test_reported_vesting_wins(self):
        self.assertEqual(equity.vested_now(grant(vested_reported=1234), TODAY), 1234)


class Base(DbCase):
    pass


class ModelTests(Base):
    def test_enter_by_hand_and_net_worth(self):
        cid = equity.save_company(self.c, {"name": "Acme", "share_price": 5})
        equity.save_grant(self.c, cid, {"kind": "iso", "quantity": 4800, "strike": 1, "vest_start": "2024-01-15",
                                        "vest_months": 48, "cliff_months": 12})
        equity.save_grant(self.c, cid, {"kind": "rsu", "quantity": 100, "vest_start": "2020-01-01", "vest_months": 12})
        o = equity.overview(self.c, TODAY)
        acme = o["companies"][0]
        self.assertEqual(next(g for g in acme["grants"] if g["kind"] == "iso")["vested"], 3200)   # 32 months in
        self.assertEqual(acme["vested_value"], 3200 * 4 + 100 * 5)
        nw = networth.summary(self.c, TODAY, save=False)
        eq = next(g for g in nw["groups"] if g["key"] == "equity")
        self.assertEqual(eq["total"], 13300.0)
        equity.save_company(self.c, {"in_networth": False}, cid)
        self.assertFalse(any(g["key"] == "equity" for g in networth.summary(self.c, TODAY, save=False)["groups"]))

    def test_checks(self):
        cid = equity.save_company(self.c, {"name": "Acme"})
        for bad, msg in [({"kind": "x", "quantity": 1}, "kind"), ({"kind": "iso", "quantity": 10}, "strike"),
                         ({"kind": "rsu", "quantity": 0}, "number of shares"),
                         ({"kind": "rsu", "quantity": 10, "vest_months": 12, "cliff_months": 24}, "cliff"),
                         ({"kind": "iso", "quantity": 10, "strike": 1, "exercised": 11}, "More exercised"),
                         ({"kind": "rsu", "quantity": 10, "vest_start": "soon"}, "date"),
                         # a length or date that runs off the calendar would stop every net-worth snapshot (the sync)
                         ({"kind": "rsu", "quantity": 10, "vest_months": 100000}, "600 months"),
                         ({"kind": "rsu", "quantity": 10, "vest_months": 12, "vest_every": 601}, "600 months"),
                         ({"kind": "rsu", "quantity": 10, "vest_start": "9999-01-01"}, "between 1900 and 2200")]:
            with self.assertRaisesRegex(equity.EquityError, msg):
                equity.save_grant(self.c, cid, bad)
        # one saved before the limits doesn't take the page (or the sync) down with it
        gid = equity.save_grant(self.c, cid, {"kind": "rsu", "quantity": 10, "vest_start": "2024-01-01", "vest_months": 12})
        self.c.execute(update(EquityGrant).where(EquityGrant.id == gid).values(vest_months=200000))
        self.c.commit()
        g = equity.overview(self.c, TODAY)["companies"][0]["grants"][0]
        self.assertEqual((g["vested"], g["fully_vested_on"], g["schedule"]), (0.0, None, []))
        self.assertIn("vesting", g["problem"])
        with self.assertRaises(equity.EquityError):
            equity.save_company(self.c, {"name": ""})


class FakeCarta:
    """Carta's API as this code expects it: portfolios -> issuers -> option grants, RSUs, certificates, FMVs."""

    def __init__(self):
        self.calls = []
        self.token_posts = []
        self.data = {
            "portfolios": {"portfolios": [{"id": "p1", "name": "Alex"}]},
            "portfolios/p1/issuers": {"issuers": [{"id": "42", "legalName": "Acme Robotics, Inc."}], "nextPageToken": None},
            "portfolios/p1/issuers/42/optionGrants": {"optionGrants": [
                {"id": "og1", "label": "ES-7", "quantity": {"value": "4800"}, "exercisePrice": {"amount": "1.00", "currency": "USD"},
                 "optionType": "ISO", "issueDate": "2024-01-10", "vestingStartDate": "2024-01-15",
                 "vestingSchedule": {"name": "1/48 monthly, 1 year cliff"}, "vestedQuantity": 3300, "exercisedQuantity": 0,
                 "grantExpirationDate": "2034-01-10"}]},
            "portfolios/p1/issuers/42/fairMarketValues": {"fairMarketValues": [
                {"effectiveDate": "2025-06-01", "value": {"amount": "3.10"}}, {"effectiveDate": "2026-03-01", "value": {"amount": "4.25"}}]},
            "portfolios/p1/issuers/42/certificates": {"certificates": [{"id": "cs9", "label": "CS-9", "quantity": 500, "issueDate": "2023-05-01"}]},
        }

    def __call__(self, req):
        url = urllib.parse.urlsplit(req.full_url)
        if req.get_method() == "POST":
            self.token_posts.append(dict(urllib.parse.parse_qsl(req.data.decode())))
            return io.BytesIO(json.dumps({"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600}).encode())
        path = url.path.split("/v1alpha1/", 1)[1]
        self.calls.append((path, req.headers.get("Authorization")))
        if path not in self.data:
            raise urllib.error.HTTPError(req.full_url, 404, "not found", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps(self.data[path]).encode())


class CartaTests(Base):
    def test_sign_in_and_sync(self):
        api = FakeCarta()
        carta.save_settings(self.c, {"env": "production", "client_id": "cid", "client_secret": "sec"})
        url = carta.authorize_url(self.c, "https://runway.example.com/carta/callback")
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        self.assertTrue(url.startswith("https://login.app.carta.com/o/authorize/"))
        self.assertEqual((qs["client_id"], qs["scope"]), ("cid", carta.SCOPES))
        with self.assertRaises(carta.CartaError):
            carta.finish_authorize(self.c, "code-1", "not-the-state", opener=api)   # a sign-in that didn't start here
        carta.authorize_url(self.c, "https://runway.example.com/carta/callback")
        state = db.get_setting(self.c, "carta_oauth_state").split(" ")[0]
        carta.finish_authorize(self.c, "code-1", state, opener=api)
        self.assertEqual(api.token_posts[0]["redirect_uri"], "https://runway.example.com/carta/callback")
        raw = self.c.execute(select(Setting.value).where(Setting.key == "carta_access_token")).fetchone()[0]
        self.assertTrue(raw.startswith("enc:"))                                     # kept encrypted
        out = carta.sync(self.c, opener=api)
        self.assertEqual(out, {"companies": 1, "grants": 2})
        self.assertTrue(all(auth == "Bearer at-1" for _, auth in api.calls))
        o = equity.overview(self.c, TODAY)
        acme = o["companies"][0]
        self.assertEqual((acme["name"], acme["share_price"], acme["price_as_of"]), ("Acme Robotics, Inc.", 4.25, "2026-03-01"))
        og = next(g for g in acme["grants"] if g["kind"] == "iso")
        self.assertEqual((og["quantity"], og["strike"], og["vest_months"], og["cliff_months"], og["vested"], og["expires_on"]),
                         (4800, 1.0, 48, 12, 3300, "2034-01-10"))
        shares = next(g for g in acme["grants"] if g["kind"] == "shares")
        self.assertEqual(shares["vested"], 500)
        self.assertEqual(acme["vested_value"], round(3300 * 3.25 + 500 * 4.25, 2))
        carta.sync(self.c, opener=api)   # again: updated, not duplicated
        self.assertEqual(self.c.execute(select(func.count()).select_from(EquityGrant)).fetchone()[0], 2)

    def test_refresh_and_refusal(self):
        api = FakeCarta()
        carta.save_settings(self.c, {"env": "production", "client_id": "cid", "client_secret": "sec"})
        db.set_setting(self.c, "carta_access_token", "old")
        db.set_setting(self.c, "carta_refresh_token", "rt-0")
        db.set_setting(self.c, "carta_token_expires", str(int(time.time()) - 10))
        carta.sync(self.c, opener=api)
        self.assertEqual(api.token_posts[0], {"grant_type": "refresh_token", "refresh_token": "rt-0"})

        def refuse(req):
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"{}"))
        with self.assertRaises(carta.CartaError):
            carta.sync(self.c, opener=refuse)
        self.assertIn("refused", db.get_setting(self.c, "carta_last_error"))

    def test_a_rotated_token_and_the_error_survive_a_failed_sync(self):
        carta.save_settings(self.c, {"env": "production", "client_id": "cid", "client_secret": "sec"})
        db.set_setting(self.c, "carta_access_token", "old")
        db.set_setting(self.c, "carta_refresh_token", "rt-0")
        db.set_setting(self.c, "carta_token_expires", str(int(time.time()) - 10))
        self.c.commit()

        def rotate_then_fail(req):   # a new refresh token, then Carta fails the portfolio read
            if req.get_method() == "POST":
                return io.BytesIO(json.dumps({"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600}).encode())
            raise urllib.error.HTTPError(req.full_url, 500, "oops", {}, io.BytesIO(b"{}"))
        with self.assertRaises(carta.CartaError):
            carta.sync(self.c, opener=rotate_then_fail)
        self.c.rollback()   # what the request's session does with an error
        self.assertEqual(db.get_setting(self.c, "carta_refresh_token"), "rt-2")
        self.assertIn("500", db.get_setting(self.c, "carta_last_error"))

    def test_playground_apps_sign_in_at_the_playground(self):
        api = FakeCarta()
        carta.save_settings(self.c, {"env": "playground", "client_id": "pg-cid", "client_secret": "pg-sec"})
        url = carta.authorize_url(self.c, "https://runway.example.com/carta/callback")
        self.assertTrue(url.startswith("https://login.playground.carta.team/o/authorize/"))
        state = db.get_setting(self.c, "carta_oauth_state").split(" ")[0]
        seen = []
        carta.finish_authorize(self.c, "code-1", state, opener=lambda req: seen.append(req.full_url) or api(req))
        carta.sync(self.c, opener=lambda req: seen.append(req.full_url) or api(req))
        self.assertEqual(seen[0], "https://login.playground.carta.team/o/access_token/")
        self.assertTrue(all(u.startswith("https://api.playground.carta.team/") for u in seen[1:]))

    def test_schedule_names(self):
        self.assertEqual(carta._schedule("1/48 monthly, 1 year cliff"), (48, 12, 1))
        self.assertEqual(carta._schedule("4 years quarterly, 12 month cliff"), (48, 12, 3))
        self.assertEqual(carta._schedule(None), (None, None, 1))


if __name__ == "__main__":
    unittest.main()
