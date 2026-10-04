"""Amounts of money, as the floats they're kept in: when two are the same to the cent, and splitting a total into whole
cents that add up to it exactly.

CENT is half a cent: amounts closer together than that round to the same cent, so they're the same amount, and one
smaller than that is nothing.
"""
from __future__ import annotations

from collections.abc import Callable

CENT = 0.005


def is_zero(amount: float) -> bool:
    """Whether an amount rounds to nothing: under half a cent either way."""
    return abs(amount) < CENT


def same_amount(a: float, b: float) -> bool:
    """Whether two amounts are the same to the cent: less than half a cent apart."""
    return abs(a - b) < CENT


def cents(amount: float) -> int:
    """An amount in whole cents, rounded to the cent first (so 0.285, which is 0.28499… as a float, is 28)."""
    return round(round(amount, 2) * 100)


def allocate_cents(shares: list[float], total: int, start: Callable[[float], int] = round) -> list[int]:
    """Whole cents for each share (each in cents, a float) that add up to `total` cents exactly, by largest remainder:
    each share starts at `start` of it (rounded, by default; `int` to start from what's whole), and the cents that leaves
    over or short go one at a time to the shares nearest to going the other way (the earliest of equal ones first, and
    round again if there are more cents than shares)."""
    if not shares:
        return []
    out = [start(s) for s in shares]
    gap = total - sum(out)
    step = 1 if gap > 0 else -1
    order = sorted(range(len(shares)), key=lambda i: (shares[i] - out[i]) * step, reverse=True)
    for k in range(abs(gap)):
        out[order[k % len(order)]] += step
    return out
