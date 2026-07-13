import re
from dataclasses import dataclass

from services.market import Market


@dataclass(frozen=True)
class ResolvedSymbol:
    base: str
    display: str
    instrument_id: str
    exchange: str = "OKX"
    instrument_type: str = "SWAP"


class SymbolResolver:
    """Normalize user input and resolve an active OKX USDT perpetual swap."""

    _ALIASES = {
        "XBT": "BTC",
        "BCC": "BCH",
    }

    def __init__(self, market: Market | None = None):
        self.market = market or Market()

    @classmethod
    def normalize(cls, raw: str) -> str:
        value = (raw or "").upper().strip()
        value = value.replace("/", "-").replace("_", "-")
        value = re.sub(r"\s+", "", value)

        for suffix in ("-USDT-SWAP", "USDT-SWAP", "-USDT", "USDT", "-PERP", "PERP"):
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[: -len(suffix)].rstrip("-")
                break

        value = cls._ALIASES.get(value, value)
        if not re.fullmatch(r"[A-Z0-9]{2,20}", value):
            raise ValueError(
                "Некорректный тикер. Примеры OKX: BTC, SUI, TAO, PEPE или BTC-USDT-SWAP"
            )
        return value

    async def resolve(self, raw: str, interval: str = "1h") -> ResolvedSymbol:
        base = self.normalize(raw)
        try:
            instrument = await self.market.provider.resolve_instrument(base)
            df = await self.market.get_klines(base, interval=interval, limit=120)
        except ValueError as exc:
            raise ValueError(
                f"Фьючерс {base}-USDT-SWAP не найден на OKX или сейчас не торгуется."
            ) from exc
        except Exception as exc:
            raise ValueError(
                f"OKX временно не отдал данные для {base}-USDT-SWAP. Попробуйте позже."
            ) from exc

        if df is None or len(df) < 50:
            raise ValueError(f"Недостаточно свечей OKX для {base}-USDT-SWAP.")

        inst_id = str(instrument["instId"])
        return ResolvedSymbol(
            base=base,
            display=f"{inst_id} · OKX Futures",
            instrument_id=inst_id,
        )
