"""Amounts to the cent (runway/money.py), and the two splits into whole cents built on it: the forecast's estimate parts
(forecast.to_cents) and a rule's split (rules.split_parts), each checked against the way it worked out cents before."""
import random
import unittest

from runway import forecast, money, rules


def old_to_cents(values: list[float], total: float) -> list[float]:
    """forecast.to_cents as it was before money.allocate_cents."""
    if not values:
        return []
    cents = [round(v * 100) for v in values]
    gap = round(round(total, 2) * 100) - sum(cents)
    step = 1 if gap > 0 else -1
    order = sorted(range(len(values)), key=lambda i: (values[i] * 100 - cents[i]) * step, reverse=True)
    for k in range(abs(gap)):
        cents[order[k % len(order)]] += step
    return [c / 100 for c in cents]


def old_split_parts(amount: float, split: list[dict]) -> list[dict]:
    """rules.split_parts as it was before money.allocate_cents."""
    cents = round(abs(amount) * 100)
    sign = -1 if amount < 0 else 1
    whole = sum(float(p["percent"]) for p in split) or 100.0
    shares = [(p["category"], cents * float(p["percent"]) / whole) for p in split]
    parts = [[c, int(s), s - int(s)] for c, s in shares]
    left = cents - sum(p[1] for p in parts)
    for p in sorted(parts, key=lambda p: -p[2])[:max(0, left)]:
        p[1] += 1
    for p in sorted(parts, key=lambda p: -p[1])[:max(0, -left)]:
        p[1] -= 1
    return [{"category": c, "amount": sign * n / 100} for c, n, _ in parts if n]


class CentTests(unittest.TestCase):
    def test_zero_and_the_same(self):
        self.assertTrue(money.is_zero(0.0))
        self.assertTrue(money.is_zero(0.0049))
        self.assertTrue(money.is_zero(-0.0049))
        self.assertFalse(money.is_zero(0.01))
        self.assertFalse(money.is_zero(-0.006))
        self.assertTrue(money.same_amount(10.0, 10.004))
        self.assertTrue(money.same_amount(0.1 + 0.2, 0.3))
        self.assertFalse(money.same_amount(10.0, 10.01))
        self.assertFalse(money.same_amount(-95.0, 95.0))

    def test_cents(self):
        self.assertEqual(money.cents(12.34), 1234)
        self.assertEqual(money.cents(-12.34), -1234)
        self.assertEqual(money.cents(0.285), 28)
        self.assertEqual(money.cents(19.999), 2000)
        self.assertEqual(money.cents(0.0), 0)


class AllocateTests(unittest.TestCase):
    def test_they_add_up(self):
        self.assertEqual(money.allocate_cents([], 0), [])
        self.assertEqual(money.allocate_cents([33.333, 33.333, 33.333], 100), [34, 33, 33])
        self.assertEqual(money.allocate_cents([50.4, 50.4], 100), [50, 50])
        self.assertEqual(money.allocate_cents([10.6, 10.6, 10.6], 31), [10, 10, 11])
        self.assertEqual(money.allocate_cents([1.5, 1.5], 3, start=int), [2, 1])
        self.assertEqual(money.allocate_cents([-20.4, 120.4], 100), [-20, 120])
        self.assertEqual(money.allocate_cents([1.0, 1.0], 7), [4, 3])

    def test_as_the_forecast_and_rules_worked_them_out(self):
        rng = random.Random(20261004)
        for _ in range(3000):
            n = rng.randint(1, 7)
            values = [round(rng.uniform(-400, 1200), rng.choice([2, 3, 6])) for _ in range(n)]
            if rng.random() < 0.3:
                values = [rng.choice([0.125, 0.375, 1.005, 2.675, 0.5, 16.666666]) for _ in range(n)]
            total = sum(values) + rng.choice([0.0, 0.0, 0.01, -0.02, 0.07])
            self.assertEqual(forecast.to_cents(values, total), old_to_cents(values, total), (values, total))
            parts = [{"category": f"C{i}", "percent": rng.choice([50, 33.33, 33.34, 25, 12.5, 0.01, rng.randint(1, 99)])}
                     for i in range(n)]
            amount = rng.choice([round(rng.uniform(-3000, 3000), 2), 0.03, -10.03, 0.01, 100.0])
            self.assertEqual(rules.split_parts(amount, parts), old_split_parts(amount, parts), (amount, parts))
