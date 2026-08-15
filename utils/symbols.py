from __future__ import annotations

import re


_BASE = re.compile(r"^[A-Z0-9]{2,20}$")


def normalize_usdt_symbol(value: str) -> str:
    """Normalize an unambiguous spot/perpetual USDT symbol without guessing another quote."""
    raw = str(value or "").strip().upper().replace("-", "").replace("_", "").replace("/", "")
    if not _BASE.fullmatch(raw):
        raise ValueError("INVALID_SYMBOL_FORMAT")
    if raw.endswith("USDT"):
        base = raw[:-4]
    elif raw not in {"BTC", "ETH"} and raw.endswith(("USD", "USDC", "BTC", "ETH")):
        raise ValueError("AMBIGUOUS_OR_UNSUPPORTED_QUOTE")
    else:
        base = raw
    if not _BASE.fullmatch(base):
        raise ValueError("INVALID_SYMBOL_FORMAT")
    return f"{base}USDT"
