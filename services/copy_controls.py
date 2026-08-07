from __future__ import annotations

import re
from typing import Any


def normalize_copy_symbol(value: Any) -> str:
    """Canonical user/control symbol identity (BTC-USDT, BTC/USDT -> BTCUSDT)."""
    raw = str(value or "").strip().upper().split(":", 1)[0]
    return re.sub(r"[^A-Z0-9]", "", raw)


def normalize_timeframe(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_setup(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())
