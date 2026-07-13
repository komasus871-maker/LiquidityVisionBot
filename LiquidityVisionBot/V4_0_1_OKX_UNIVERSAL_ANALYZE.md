# Liquidity Vision v4.0.1 — OKX Universal Analyze

- Universal Analyze now resolves active OKX `USDT-SWAP` perpetual futures.
- `BTC`, `BTCUSDT`, `BTC-USDT`, and `BTC-USDT-SWAP` normalize to `BTC-USDT-SWAP`.
- Instrument existence is verified through OKX public instruments data.
- Candles are loaded from OKX market history endpoints only.
- The instrument list is cached and refreshed automatically.
- Candle pagination supports up to 1,000 historical bars.
- Reports and prompts identify OKX Futures explicitly.
- Unsupported or delisted OKX swap instruments return a clear user-facing error.
