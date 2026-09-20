# Flow / Microstructure Data Contract

Status: frozen research contract `flow-microstructure-v1`. It has no execution authority.

## Certified historical source

The primary source is the official Binance public USD-M futures archive for `BTCUSDT`, `ETHUSDT`, and `SOLUSDT`. The 5m kline schema is sourced from `/fapi/v1/klines` and contains exchange-reported total base/quote volume, trade count, taker-buy base volume, and taker-buy quote volume. This is genuine interval-aggregated aggressor flow; it is not inferred from candle direction.

| Type | Archive | First retained BTC / ETH / SOL | Sampling | Primary-cycle status |
|---|---|---|---|---|
| 5m taker flow/OHLCV | `futures/um/monthly/klines/{symbol}/5m` | 2020-01 / 2020-01 / 2020-09 | closed 5m interval | CERTIFIED |
| aggregate trades | `futures/um/monthly/aggTrades/{symbol}` | 2020-01 / 2020-01 / 2020-09 | event | AVAILABLE; not materialized due size |
| individual trades | `futures/um/monthly/trades/{symbol}` | 2019-09 / 2019-11 / 2020-09 | event | AVAILABLE; not materialized due size |
| OI/positioning | `futures/um/daily/metrics/{symbol}` | 2020-09 / 2021-12 / 2021-12 | 5m stock | CERTIFIED |
| settled funding | `futures/um/monthly/fundingRate/{symbol}` | 2020-01 / 2020-01 / 2020-09 | event | CERTIFIED |
| bucketed book depth | `futures/um/daily/bookDepth/{symbol}` | 2023-01-01 all three | roughly 30s, percentage buckets | AVAILABLE but excluded |
| best-book ticker | `futures/um/monthly/bookTicker/{symbol}` | 2023-05 all three | event | AVAILABLE but impractical multi-GB files |
| liquidation events | no USD-M public archive prefix | unavailable | unavailable | INSUFFICIENT |

All archives are public and require no account, trading access, or credentials. Pagination is deterministic monthly/daily filenames. The bulk archive has no published endpoint-specific request quota; bounded parallel reads, retry/backoff, and persistent-error `PROVIDER_ERROR` semantics apply. Every downloaded file contributes its URL, bytes, SHA-256, retrieval time, and component aggregate hash. Missing files are errors, never zero-flow intervals.

## Trade and aggressor semantics

For event-level trades, `isBuyerMaker=true` means the buyer supplied resting liquidity, so the seller was the aggressor; `false` means the buyer was the aggressor. Quantity is base-asset quantity and quote notional is price times quantity.

For certified 5m aggregates:

- `taker_buy_base` and `taker_buy_quote` are exchange-reported buyer-aggressor volume.
- `taker_sell_base = total_base_volume - taker_buy_base`.
- `taker_sell_quote = total_quote_volume - taker_buy_quote`.
- `delta_quote = taker_buy_quote - taker_sell_quote`.
- `normalized_delta = delta_quote / total_quote_volume` when total quote volume is positive.
- `CVD` is the causal cumulative sum of `delta_quote`; rolling CVD change is preferred over absolute level.

Negative volumes, taker volume above total beyond floating tolerance, nonfinite values, duplicate interval opens, or incomplete intervals are `CONFLICTING`. Trade count has no aggressor split in the kline schema, so no trade-count imbalance is claimed. Large-trade features require event archives and are disabled.

## Time and causal availability

`open_time` is interval start; `close_time` is the final millisecond of the interval. A 5m record becomes available at the next interval boundary. A decision at `T` may use only records with `close_time < T` and availability `<= T`. Fifteen-minute bars aggregate exactly three completed 5m records. One-hour context aggregates exactly twelve completed 5m records and becomes visible only after the hour closes.

OI `create_time` is its source and conservative availability time. Backward as-of alignment permits only `available_at <= decision_at`, with maximum age six minutes. Settled funding becomes available at `calc_time`, backward-held for no more than 12 hours. No nearest-future lookup, centered rolling window, or interpolation is permitted.

## Book and liquidation semantics

Archived `bookDepth` reports depth/notional in percentage-distance buckets around the market, not a raw reconstructable L2 book. The archive is large and its bucket/snapshot contract is unsuitable for the bounded primary strategy experiment. It may inform a separately certified future study. Best-book event files are likewise excluded from the historical cycle due multi-gigabyte monthly files.

No trustworthy USD-M liquidation archive was discovered. Absence of a liquidation record is therefore unknown, not zero. Liquidation families are disabled. A public live collector may archive force-order streams prospectively, but those observations cannot be backfilled.

## Integrity and fail-closed behavior

Statuses are `VALID`, `STALE`, `GAPPED`, `INSUFFICIENT`, `CONFLICTING`, `PROVIDER_ERROR`, and `UNKNOWN`. Checks cover chronology, exact interval coverage, duplicates, future timestamps, NaN/inf, impossible volumes, source symbols, units, schema changes, OI gaps, and funding age. Required non-`VALID` inputs produce `NO_SIGNAL / DATA_INVALID`.

Venue remains explicit as `BINANCE_UM`. These findings cannot be assumed transferable to OKX/BingX without an overlapping later transferability test after historical qualification.
