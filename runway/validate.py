"""Checking what someone typed or sent: numbers, whole numbers, short texts, days and on/off flags.

Each module that takes input keeps its own error class and wording, so a Validator is made with both; the checks
themselves are shared. Nothing is rounded or normalised on the way in: a number is db.number's (which refuses nan, inf
and anything past MAX_NUMBER), after dropping only the characters asked for (a pasted "$1,234.50").
"""
from __future__ import annotations

import math
from datetime import date
from typing import Literal, overload

from . import db

TRUE = (True, 1, "1", "true", "on")


def parse_number(v, drop: str = ",$") -> float:
    """v as a number. drop: characters removed first (from str(v), then stripped), so True is "True" and refused.
    drop="" passes v to db.number as it is (None is a TypeError, True is 1.0). Raises TypeError or ValueError like
    db.number."""
    if not drop:
        return db.number(v)
    s = str(v)
    for c in drop:
        s = s.replace(c, "")
    return db.number(s.strip())


def flag(v) -> int:
    """1 for a checkbox or switch that's on (True, 1, "1", "true", "on"), else 0."""
    return 1 if v in TRUE else 0


class Validator:
    """The shared checks, raising `error` with a module's own messages. Templates are str.format()ted with label (and
    low and high, or limit). Left out or empty (None or ""), a value is None, or the `missing` error when required."""

    def __init__(self, error: type[Exception], *, drop: str = ",$",
                 missing: str = "Enter the {label}",
                 not_number: str = "The {label} must be a number",
                 out_of_range: str = "The {label} must be between {low:g} and {high:g}",
                 not_whole: str = "The {label} must be a whole number",
                 too_long: str = "The {label} is too long (at most {limit} characters)",
                 not_date: str = "The {label} must be a date (YYYY-MM-DD)"):
        self.error = error
        self.drop = drop
        self.missing = missing
        self.not_number = not_number
        self.out_of_range = out_of_range
        self.not_whole = not_whole
        self.too_long = too_long
        self.not_date = not_date

    @overload
    def number(self, v, label: str, low: float = ..., high: float = ..., *, required: Literal[True]) -> float: ...
    @overload
    def number(self, v, label: str, low: float = ..., high: float = ..., *, required: bool = ...) -> float | None: ...

    def number(self, v, label: str, low: float = -math.inf, high: float = math.inf, *,
               required: bool = False) -> float | None:
        """A number from low to high (both included)."""
        if v in (None, ""):
            if required:
                raise self.error(self.missing.format(label=label))
            return None
        try:
            n = parse_number(v, self.drop)
        except (TypeError, ValueError):
            raise self.error(self.not_number.format(label=label)) from None
        if not low <= n <= high:
            raise self.error(self.out_of_range.format(label=label, low=low, high=high))
        return n

    @overload
    def integer(self, v, label: str, low: int, high: int, *, required: Literal[True]) -> int: ...
    @overload
    def integer(self, v, label: str, low: int, high: int, *, required: bool = ...) -> int | None: ...

    def integer(self, v, label: str, low: int, high: int, *, required: bool = False) -> int | None:
        """A whole number from low to high: 3 and "3.0" are 3, 3.5 is refused (not rounded)."""
        n = self.number(v, label, low, high, required=required)
        if n is not None and n != int(n):
            raise self.error(self.not_whole.format(label=label))
        return None if n is None else int(n)

    def text(self, v, label: str, limit: int, required: bool = False) -> str | None:
        """A text of at most limit characters, without the spaces around it; None when there's nothing."""
        s = str(v or "").strip()
        if required and not s:
            raise self.error(self.missing.format(label=label))
        if len(s) > limit:
            raise self.error(self.too_long.format(label=label, limit=limit))
        return s or None

    def day(self, v, label: str, required: bool = False) -> str | None:
        """A day as YYYY-MM-DD; None when there's nothing."""
        s = str(v or "").strip()
        if not s:
            if required:
                raise self.error(self.missing.format(label=label))
            return None
        try:
            return date.fromisoformat(s).isoformat()
        except ValueError:
            raise self.error(self.not_date.format(label=label)) from None
