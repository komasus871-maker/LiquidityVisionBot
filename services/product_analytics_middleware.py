from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from services.product_analytics import ProductAnalyticsService


class ProductAnalyticsMiddleware(BaseMiddleware):
    """Record command-level product usage without storing text or arguments."""

    def __init__(self) -> None:
        self.analytics = ProductAnalyticsService()

    async def __call__(self, handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
                       event: Message, data: dict[str, Any]) -> Any:
        text = str(getattr(event, "text", "") or "").strip()
        command = None
        if text.startswith("/"):
            command = text.split(maxsplit=1)[0].split("@", 1)[0][1:].lower()[:64]
        try:
            result = await handler(event, data)
        except Exception:
            if command:
                self._record(event, command, "HANDLER_ERROR")
            raise
        if command:
            self._record(event, command, "USED")
        return result

    def _record(self, event: Message, command: str, outcome: str) -> None:
        try:
            user = getattr(event, "from_user", None)
            chat = getattr(event, "chat", None)
            self.analytics.record(
                getattr(user, "id", None), f"COMMAND:{command}", outcome=outcome,
                metadata={"chat_type": str(getattr(chat, "type", "unknown"))[:24]},
            )
        except Exception:
            logging.exception("Product analytics command recording failed")
