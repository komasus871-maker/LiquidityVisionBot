from __future__ import annotations


def decimals_for_price(value: float) -> int:
    value = abs(float(value))
    if value == 0:
        return 2
    if value >= 1000:
        return 2
    if value >= 100:
        return 3
    if value >= 1:
        return 4
    if value >= 0.1:
        return 5
    if value >= 0.01:
        return 6
    if value >= 0.001:
        return 7
    if value >= 0.0001:
        return 8
    if value >= 0.00001:
        return 10
    if value >= 0.000001:
        return 11
    return 12


def fmt_price(value: float) -> str:
    value = float(value)
    if value == 0:
        return "0"
    decimals = decimals_for_price(value)
    text = f"{value:.{decimals}f}"
    trimmed = text.rstrip("0").rstrip(".") if "." in text else text
    if trimmed in {"0", "-0"} and value != 0:
        return f"{value:.12f}".rstrip("0").rstrip(".")
    return trimmed


def fmt_number(value: float, decimals: int = 2) -> str:
    return f"{float(value):.{decimals}f}".rstrip("0").rstrip(".")
