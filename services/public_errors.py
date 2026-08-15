from __future__ import annotations

from typing import Any


PUBLIC_MESSAGES = {
    "MARKET": "Market data is temporarily unavailable. Try again shortly.",
    "ANALYSIS": "Analysis is temporarily unavailable. Try again shortly.",
    "EXCHANGE": "The exchange request could not be completed. Check the connection and try again.",
    "ACCOUNT": "Account data is temporarily unavailable. No order was submitted.",
    "RESEARCH": "That research view is temporarily unavailable.",
    "COPY": "The PAPER copy request could not be completed. No live trade was submitted.",
    "SYSTEM": "The service is temporarily unavailable. Try again shortly.",
}


def public_error_message(error: Any = None, *, context: str = "SYSTEM") -> str:
    """Return stable public prose; intentionally never render provider exception text."""
    del error
    return PUBLIC_MESSAGES.get(str(context).upper(), PUBLIC_MESSAGES["SYSTEM"])
