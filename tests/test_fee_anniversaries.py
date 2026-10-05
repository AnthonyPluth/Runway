"""A card's annual-fee anniversaries (churning.fee_anniversaries), which the Churning page's next fee and the forecast's
fees both count from: checked against the way each worked them out before they shared it."""
import random
import unittest
from datetime import date, timedelta

from sqlalchemy import insert

from runway.domain import churning, forecast
from runway import dates
from runway.storage.models import ChurnCard
from tests.shared import LedgerCase


def old_next_fee(opened: date, today: date) -> date | None:
    """churning.next_fee's anniversary, as it was."""
    for year in range(max(opened.year, today.year - 1), today.year + 2):
        d = dates.clamp_day(year, opened.month, opened.day)
        if d > opened and d >= today:
            return d
    return None


def old_forecast_days(opened: date, today: date, end: date) -> list[date]:
    """The anniversaries forecast.annual_fees looked at, as it was (before what it then leaves out)."""
    out = []
    for year in range(max(opened.year + 1, today.year), end.year + 1):
        if dates.clamp_day(year, opened.month, 31) < today:
            continue
        day = dates.clamp_day(year, opened.month, opened.day)
        if day > end:
            break
        out.append(day)
    return out


def new_forecast_days(opened: date, today: date, end: date) -> list[date]:
    out = []
    for day in churning.fee_anniversaries(opened, dates.month_start(today)):
        if day > end:
            break
        out.append(day)
    return out


class FeeAnniversaryTests(unittest.TestCase):
    def test_each_anniversary_from_the_first(self):
        days = churning.fee_anniversaries(date(2024, 2, 29), date(2024, 1, 1))
        self.assertEqual([next(days) for _ in range(5)],
                         [date(2025, 2, 28), date(2026, 2, 28), date(2027, 2, 28), date(2028, 2, 29), date(2029, 2, 28)])
        self.assertEqual(next(churning.fee_anniversaries(date(2024, 10, 10), date(2026, 10, 10))), date(2026, 10, 10))
        self.assertEqual(next(churning.fee_anniversaries(date(2024, 10, 10), date(2026, 10, 11))), date(2027, 10, 10))
        self.assertEqual(next(churning.fee_anniversaries(date(2026, 10, 10), date(2026, 1, 1))), date(2027, 10, 10))
        self.assertEqual(list(churning.fee_anniversaries(date(9997, 5, 1), date(9998, 6, 1))), [date(9999, 5, 1)])

    def test_the_churning_pages_next_fee_is_as_it_was(self):
        rng = random.Random(4)
        for _ in range(4000):
            opened = date(2018, 1, 1) + timedelta(days=rng.randint(0, 4000))
            today = date(2020, 1, 1) + timedelta(days=rng.randint(0, 3000))
            card = {"annual_fee": 95.0, "opened_on": opened.isoformat()}
            self.assertEqual(churning.next_fee(card, today), old_next_fee(opened, today), (opened, today))

    def test_the_forecasts_anniversaries_are_as_they_were(self):
        rng = random.Random(5)
        for _ in range(4000):
            opened = date(2018, 1, 1) + timedelta(days=rng.randint(0, 4000))
            today = date(2020, 1, 1) + timedelta(days=rng.randint(0, 3000))
            end = today + timedelta(days=rng.choice([14, 30, 90, 365, 400, 800]))
            self.assertEqual(new_forecast_days(opened, today, end), old_forecast_days(opened, today, end), (opened, today, end))


class BothPagesTests(LedgerCase):
    def test_the_forecast_dates_a_fee_as_the_churning_page_does(self):
        self.acct("chk", "checking", 1000.0)
        self.acct("cc", "credit", 0.0, pay_from="chk")
        for opened in ("2024-02-29", "2024-10-31", "2025-09-23", "2023-09-24", "2025-12-31"):
            self.conn.execute(insert(ChurnCard).values(owner="Alex", issuer="chase", product=f"Card {opened}", opened_on=opened,
                                                       annual_fee=95.0, account_id="cc"))
        today = date(2026, 9, 23)
        fc = forecast.build(self.conn, today, 400)
        for opened in ("2024-02-29", "2024-10-31", "2025-09-23", "2023-09-24", "2025-12-31"):
            with self.subTest(opened=opened):
                due = churning.next_fee({"annual_fee": 95.0, "opened_on": opened}, today)
                self.assertEqual(next(f["date"] for f in fc["fees"] if f["name"] == f"Card {opened} annual fee"), due.isoformat())
