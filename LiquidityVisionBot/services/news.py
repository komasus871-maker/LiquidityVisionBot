from __future__ import annotations

import asyncio
import hashlib
import html
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp


DEFAULT_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
]

COIN_ALIASES: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "SOL": ("solana", "sol"),
    "XRP": ("xrp", "ripple"),
    "BNB": ("bnb", "binance coin"),
    "DOGE": ("dogecoin", "doge"),
    "ADA": ("cardano", "ada"),
    "AVAX": ("avalanche", "avax"),
    "LINK": ("chainlink", "link"),
    "TON": ("toncoin", "telegram open network", "ton"),
}

HIGH_IMPACT = (
    "fomc", "federal reserve", "interest rate", "rate decision", "cpi", "inflation",
    "sec", "etf", "hack", "exploit", "bankruptcy", "liquidation", "tariff",
    "executive order", "regulation", "lawsuit", "approval", "rejection",
)
MEDIUM_IMPACT = (
    "partnership", "upgrade", "mainnet", "testnet", "listing", "delisting", "inflows",
    "outflows", "whale", "funding", "open interest", "staking", "token unlock",
)
BULLISH_WORDS = (
    "approval", "approved", "inflows", "adoption", "launch", "partnership", "upgrade",
    "record high", "surge", "rally", "buyback", "accumulation", "cuts rates", "rate cut",
)
BEARISH_WORDS = (
    "hack", "exploit", "lawsuit", "rejection", "rejected", "outflows", "bankruptcy",
    "liquidation", "sell-off", "crash", "delisting", "ban", "tariff", "raises rates",
)


@dataclass(slots=True)
class NewsItem:
    id: str
    title: str
    url: str
    source: str
    published_at: str | None
    age_minutes: int | None
    impact: str
    sentiment: str
    confidence: int
    coins: list[str]
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class NewsEngine:
    """RSS news aggregation with deterministic impact classification and caching."""

    def __init__(self, cache_ttl: int = 300):
        self.cache_ttl = max(60, cache_ttl)
        self._cache: list[dict[str, Any]] = []
        self._cache_at = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _feeds() -> list[tuple[str, str]]:
        raw = os.getenv("NEWS_FEEDS", "").strip()
        if not raw:
            return DEFAULT_FEEDS
        feeds: list[tuple[str, str]] = []
        for part in raw.split(","):
            if "|" not in part:
                continue
            name, url = part.split("|", 1)
            if name.strip() and url.strip().startswith(("https://", "http://")):
                feeds.append((name.strip(), url.strip()))
        return feeds or DEFAULT_FEEDS

    @staticmethod
    def _text(element: ET.Element | None, names: tuple[str, ...]) -> str:
        if element is None:
            return ""
        for child in element.iter():
            tag = child.tag.split("}")[-1].lower()
            if tag in names and child.text:
                return child.text.strip()
        return ""

    @staticmethod
    def _clean(value: str, limit: int = 260) -> str:
        value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
        value = re.sub(r"\s+", " ", value).strip()
        return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"

    @staticmethod
    def _published(raw: str) -> tuple[str | None, int | None]:
        if not raw:
            return None, None
        parsed: datetime | None = None
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None, None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
        age = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds() // 60))
        return parsed.isoformat(), age

    @staticmethod
    def _classify(title: str, summary: str, source: str) -> tuple[str, str, int, list[str]]:
        text = f"{title} {summary}".lower()
        coins = [symbol for symbol, aliases in COIN_ALIASES.items() if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in aliases)]
        high_hits = sum(keyword in text for keyword in HIGH_IMPACT)
        medium_hits = sum(keyword in text for keyword in MEDIUM_IMPACT)
        bull_hits = sum(keyword in text for keyword in BULLISH_WORDS)
        bear_hits = sum(keyword in text for keyword in BEARISH_WORDS)

        if high_hits or source == "Federal Reserve":
            impact = "🔴 HIGH"
        elif medium_hits or coins:
            impact = "🟡 MEDIUM"
        else:
            impact = "⚪ LOW"

        delta = bull_hits - bear_hits
        if delta > 0:
            sentiment = "🟢 Bullish"
        elif delta < 0:
            sentiment = "🔴 Bearish"
        else:
            sentiment = "⚪ Neutral"

        confidence = 45 + min(35, (high_hits * 12 + medium_hits * 6 + abs(delta) * 9))
        if coins:
            confidence += min(10, len(coins) * 3)
        return impact, sentiment, min(95, confidence), coins

    async def _fetch_feed(self, session: aiohttp.ClientSession, source: str, url: str) -> list[NewsItem]:
        headers = {"User-Agent": "LiquidityVision/3.3 (+Telegram market intelligence)"}
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as response:
                response.raise_for_status()
                payload = await response.text(errors="ignore")
        except Exception:
            return []

        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return []

        nodes = [node for node in root.iter() if node.tag.split("}")[-1].lower() in {"item", "entry"}]
        results: list[NewsItem] = []
        for node in nodes[:20]:
            title = self._clean(self._text(node, ("title",)), 180)
            link = self._text(node, ("link",))
            if not link:
                for child in node.iter():
                    if child.tag.split("}")[-1].lower() == "link" and child.attrib.get("href"):
                        link = child.attrib["href"].strip()
                        break
            summary = self._clean(self._text(node, ("description", "summary", "content", "encoded")))
            raw_date = self._text(node, ("pubdate", "published", "updated", "date"))
            published_at, age_minutes = self._published(raw_date)
            if not title or not link:
                continue
            impact, sentiment, confidence, coins = self._classify(title, summary, source)
            item_id = hashlib.sha1(f"{source}|{title}|{link}".encode("utf-8")).hexdigest()[:16]
            results.append(NewsItem(item_id, title, link, source, published_at, age_minutes, impact, sentiment, confidence, coins, summary))
        return results

    async def latest(self, limit: int = 12, force: bool = False) -> list[dict[str, Any]]:
        if not force and self._cache and (time.time() - self._cache_at) < self.cache_ttl:
            return self._cache[:limit]
        async with self._lock:
            if not force and self._cache and (time.time() - self._cache_at) < self.cache_ttl:
                return self._cache[:limit]
            connector = aiohttp.TCPConnector(limit=8, ttl_dns_cache=300)
            async with aiohttp.ClientSession(connector=connector) as session:
                batches = await asyncio.gather(*(self._fetch_feed(session, name, url) for name, url in self._feeds()))
            deduped: dict[str, NewsItem] = {}
            for item in (entry for batch in batches for entry in batch):
                key = re.sub(r"\W+", "", item.title.lower())[:100]
                previous = deduped.get(key)
                if previous is None or (item.age_minutes or 10**9) < (previous.age_minutes or 10**9):
                    deduped[key] = item
            items = list(deduped.values())
            impact_rank = {"🔴 HIGH": 0, "🟡 MEDIUM": 1, "⚪ LOW": 2}
            items.sort(key=lambda x: (impact_rank.get(x.impact, 3), x.age_minutes if x.age_minutes is not None else 10**9))
            self._cache = [item.as_dict() for item in items[:40]]
            self._cache_at = time.time()
            return self._cache[:limit]

    async def high_impact(self, limit: int = 5) -> list[dict[str, Any]]:
        return [item for item in await self.latest(limit=30) if item["impact"] == "🔴 HIGH"][:limit]
