from __future__ import annotations

import asyncio
import html
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable

import aiohttp


DEFAULT_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

COIN_KEYWORDS = {
    "BTC": ("bitcoin", "btc"),
    "ETH": ("ethereum", "ether", "eth"),
    "SOL": ("solana", "sol"),
    "BNB": ("binance coin", "bnb"),
    "XRP": ("xrp", "ripple"),
    "DOGE": ("dogecoin", "doge"),
    "ADA": ("cardano", "ada"),
    "AVAX": ("avalanche", "avax"),
    "LINK": ("chainlink", "link"),
    "TON": ("toncoin", "telegram open network"),
}

HIGH_MARKET_PHRASES = (
    "fomc", "federal reserve rate", "interest rate decision", "cpi", "consumer price index",
    "ppi", "nonfarm payroll", "nfp", "sec approves", "sec rejects", "spot bitcoin etf",
    "spot ethereum etf", "exchange hack", "bridge exploit", "bankruptcy filing", "trading halted",
    "major liquidation", "crypto ban", "emergency fork", "network outage",
)
MEDIUM_MARKET_PHRASES = (
    "etf inflow", "etf outflow", "institutional purchase", "whale transfer", "mainnet upgrade",
    "hard fork", "token unlock", "airdrop", "partnership", "exchange listing", "exchange delisting",
    "banking charter", "stablecoin regulation", "mica", "cftc", "lawsuit",
)
CRYPTO_CONTEXT = (
    "bitcoin", "btc", "ethereum", "ether", "eth", "crypto", "blockchain", "stablecoin",
    "token", "defi", "exchange", "binance", "coinbase", "solana", "xrp", "etf",
)
BULLISH_PHRASES = (
    "approved", "approval", "record inflow", "inflows rise", "adoption", "launches", "upgrade successful",
    "buys bitcoin", "purchase bitcoin", "banking charter approval", "partnership", "integrates",
)
BEARISH_PHRASES = (
    "rejected", "outflow", "hack", "exploit", "lawsuit", "ban", "network outage", "bankruptcy",
    "delisting", "liquidation", "stolen", "scam", "fraud", "security breach", "charges filed",
)
NEUTRALIZING_PHRASES = (
    "clears hurdle", "wins approval", "final approval", "recognizes authority", "to find bugs",
    "security audit", "oversight board", "researchers warn",
)



@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published_at: str
    impact: str
    sentiment: str
    confidence: int
    coins: list[str]


class NewsEngine:
    def __init__(self, cache_ttl: int | None = None):
        self.cache_ttl = cache_ttl or int(os.getenv("NEWS_CACHE_SECONDS", "300"))
        configured = [x.strip() for x in os.getenv("NEWS_FEEDS", "").split(",") if x.strip()]
        self.feeds = configured or DEFAULT_FEEDS
        self._cache: list[dict] = []
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _text(node: ET.Element | None, names: Iterable[str]) -> str:
        if node is None:
            return ""
        for name in names:
            found = node.find(name)
            if found is not None and found.text:
                return found.text.strip()
        return ""

    @staticmethod
    def _strip_markup(value: str) -> str:
        value = html.unescape(value or "")
        value = re.sub(r"<[^>]+>", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _published(raw: str) -> str:
        if not raw:
            return ""
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
            except Exception:
                return raw[:40]

    @staticmethod
    def _classify(title: str) -> tuple[str, str, int, list[str]]:
        lowered = title.lower()
        coins = [coin for coin, keywords in COIN_KEYWORDS.items() if any(k in lowered for k in keywords)]
        crypto_relevance = sum(1 for x in CRYPTO_CONTEXT if x in lowered) + (2 if coins else 0)
        high_hits = sum(1 for x in HIGH_MARKET_PHRASES if x in lowered)
        medium_hits = sum(1 for x in MEDIUM_MARKET_PHRASES if x in lowered)
        bullish_hits = sum(1 for x in BULLISH_PHRASES if x in lowered)
        bearish_hits = sum(1 for x in BEARISH_PHRASES if x in lowered)
        if any(x in lowered for x in NEUTRALIZING_PHRASES):
            bearish_hits = max(0, bearish_hits - 1)

        # Macro events can be high impact without naming crypto; ordinary AI/banking stories cannot.
        macro_high = any(x in lowered for x in ("fomc", "interest rate decision", "consumer price index", "nonfarm payroll", "cpi", "ppi"))
        if high_hits and (crypto_relevance >= 1 or macro_high):
            impact = "🔴 HIGH"
        elif medium_hits and crypto_relevance >= 1:
            impact = "🟡 MEDIUM"
        else:
            impact = "⚪ LOW"

        if bullish_hits > bearish_hits:
            sentiment = "🟢 Bullish"
        elif bearish_hits > bullish_hits:
            sentiment = "🔴 Bearish"
        else:
            sentiment = "⚪ Neutral"

        impact_bonus = 25 if impact == "🔴 HIGH" else 13 if impact == "🟡 MEDIUM" else 0
        confidence = 45 + impact_bonus + min(12, crypto_relevance * 3) + min(12, abs(bullish_hits - bearish_hits) * 6)
        confidence = max(45, min(92, confidence))
        if not coins and (crypto_relevance or macro_high):
            coins = ["MARKET"]
        return impact, sentiment, confidence, coins

    async def _fetch_feed(self, session: aiohttp.ClientSession, url: str) -> list[NewsItem]:
        headers = {"User-Agent": "LiquidityVision/3.3 (+Telegram market intelligence)"}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as response:
            response.raise_for_status()
            payload = await response.text(errors="ignore")

        root = ET.fromstring(payload)
        source = self._text(root.find("channel"), ("title",)) or re.sub(r"^https?://", "", url).split("/")[0]
        nodes = root.findall(".//item")
        if not nodes:
            nodes = root.findall("{http://www.w3.org/2005/Atom}entry")

        items: list[NewsItem] = []
        for node in nodes[:20]:
            title = self._strip_markup(self._text(node, ("title", "{http://www.w3.org/2005/Atom}title")))
            link = self._text(node, ("link",))
            if not link:
                link_node = node.find("{http://www.w3.org/2005/Atom}link")
                link = link_node.attrib.get("href", "") if link_node is not None else ""
            date = self._text(node, ("pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated"))
            if not title:
                continue
            impact, sentiment, confidence, coins = self._classify(title)
            items.append(NewsItem(title, link, source, self._published(date), impact, sentiment, confidence, coins))
        return items

    async def latest(self, limit: int = 12, force_refresh: bool = False) -> list[dict]:
        now = time.monotonic()
        if self._cache and not force_refresh and now - self._cached_at < self.cache_ttl:
            return self._cache[:limit]

        async with self._lock:
            now = time.monotonic()
            if self._cache and not force_refresh and now - self._cached_at < self.cache_ttl:
                return self._cache[:limit]

            connector = aiohttp.TCPConnector(limit=6, ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                results = await asyncio.gather(
                    *(self._fetch_feed(session, url) for url in self.feeds),
                    return_exceptions=True,
                )

            merged: list[NewsItem] = []
            for result in results:
                if isinstance(result, list):
                    merged.extend(result)

            seen: set[str] = set()
            unique: list[NewsItem] = []
            for item in merged:
                key = re.sub(r"[^a-z0-9]+", "", item.title.lower())[:160]
                if not key or key in seen:
                    continue
                seen.add(key)
                unique.append(item)

            priority = {"🔴 HIGH": 0, "🟡 MEDIUM": 1, "⚪ LOW": 2}
            unique.sort(key=lambda x: (priority.get(x.impact, 3), x.published_at), reverse=False)
            self._cache = [asdict(x) for x in unique[:50]]
            self._cached_at = time.monotonic()
            return self._cache[:limit]
