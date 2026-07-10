from __future__ import annotations


def decimals_for_price(value: float) -> int:
    value = abs(float(value))
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
    return 8


def fmt_price(value: float) -> str:
    value = float(value)
    decimals = decimals_for_price(value)
    text = f"{value:.{decimals}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_number(value: float, decimals: int = 2) -> str:
    return f"{float(value):.{decimals}f}".rstrip("0").rstrip(".")
