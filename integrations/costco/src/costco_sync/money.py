"""Decimal money helpers. Sheet cells store plain text, not binary floats."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from statistics import median

CENT = Decimal("0.01")
# Ignore shelf noise of a few cents. A real reduction is at least ten cents.
REDUCTION_THRESHOLD = Decimal("0.10")


def to_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def money(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value.quantize(CENT, rounding=ROUND_HALF_UP):f}"


def parse_money(text: str) -> Decimal | None:
    text = (text or "").strip().replace("$", "").replace(",", "")
    if not text:
        return None
    return to_decimal(text)


def median_money(samples: list[Decimal]) -> Decimal | None:
    if not samples:
        return None
    mid = Decimal(str(median(samples)))
    return mid.quantize(CENT, rounding=ROUND_HALF_UP)


def quantity_text(value: Decimal) -> str:
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")
