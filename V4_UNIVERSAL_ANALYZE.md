# Liquidity Vision v4.0.1 — OKX Universal Analyze Foundation

## Added
- Analyze any active OKX USDT perpetual swap by ticker.
- Symbol normalization (`BTC`, `BTCUSDT`, `BTC-USDT`, `BTC-USDT-SWAP`).
- Validation against OKX public `SWAP` instruments before analysis.
- OKX candle history with pagination for up to 1,000 bars.
- Timeframe selector: 15m, 1h, 4h, 1d.
- `/analyze SYMBOL TIMEFRAME` command.
- `/price SYMBOL` now uses OKX Futures ticker data.
- Per-user Watchlist stored in SQLite.
- `⭐ Watch` action under analysis.
- `⭐ Watchlist` main-menu section.

## Notes
The watchlist is persisted and prepared for autonomous OKX monitoring.
