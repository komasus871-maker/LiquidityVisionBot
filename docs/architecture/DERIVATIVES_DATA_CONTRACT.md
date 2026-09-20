# Derivatives Data Contract

Status: frozen research contract (`derivatives-alpha-v1`). This contract grants no execution authority.

## Provenance and capability audit

The primary historical provider is the official Binance public-data archive. The primary runtime venue remains OKX; this research cycle does not silently combine OKX observations with Binance observations. Every row identifies `BINANCE_VISION_UM` and the `BTCUSDT`, `ETHUSDT`, or `SOLUSDT` USD-margined perpetual contract. The local names `BTC-USDT-SWAP`, `ETH-USDT-SWAP`, and `SOL-USDT-SWAP` are aliases recorded in metadata, not claims that Binance and OKX contracts are identical.

Official archive prefixes and empirically listed first retained files on 2026-09-19:

| Field | Archive / endpoint | BTC first | ETH first | SOL first | Frequency |
|---|---|---:|---:|---:|---:|
| Perpetual OHLCV | `futures/um/monthly/klines/{symbol}/1h` | 2020-01 | 2020-01 | 2020-09 | 1h |
| Mark OHLC | `futures/um/monthly/markPriceKlines/{symbol}/1h` | 2020-01 | 2020-01 | 2020-09 | 1h |
| Index OHLC | `futures/um/monthly/indexPriceKlines/{symbol}/1h` | 2020-01 | 2020-01 | 2020-09 | 1h |
| Premium-index OHLC | `futures/um/monthly/premiumIndexKlines/{symbol}/1h` | 2020-01 | 2020-01 | 2020-09 | 1h |
| Settled funding | `futures/um/monthly/fundingRate/{symbol}` | 2020-01 | 2020-01 | 2020-09 | event, usually 8h |
| OI and positioning metrics | `futures/um/daily/metrics/{symbol}` | 2020-09-01 | 2021-12-01 | 2021-12-01 | 5m |
| Spot OHLCV | `spot/monthly/klines/{symbol}/1h` | 2017-08 | 2017-08 | 2020-08 | 1h |

The archive is public and needs no account, API key, private credential, or trading permission. Files are immutable inputs in this project: URL, byte length, SHA-256, retrieval time, and aggregate source hash are recorded. Monthly files are published after month end; they are valid for historical replay, not same-day runtime ingestion. Pagination is by deterministic daily/monthly file name rather than REST cursors. The bulk archive publishes no endpoint-specific request quota; the materializer is bounded to 16 concurrent reads, retries transient HTTP failures with backoff, and treats a persistent throttle/error as `PROVIDER_ERROR`. Missing files are explicit errors, never zeros.

OKX was audited first. Its public endpoints include current OI (`/api/v5/public/open-interest`), OI history (`/api/v5/rubik/stat/contracts/open-interest-history`), funding history (`/api/v5/public/funding-rate-history`), premium history (`/api/v5/public/premium-history`), mark/index/perpetual history, liquidation orders, taker volume, and long/short ratio. Public REST limits are IP-based and endpoint-specific; callers must use the current official endpoint limit, with HTTP/code throttles treated as provider errors. Empirical 2026-09-19 probes found OI history at 1h available 60 days back but not 90, settled funding available 90 days back but not 120, and premium available at least 180 days back. OI uses explicit `begin/end` windows; funding/premium use `before/after` cursors; price histories use timestamp cursors. That overlap is insufficient for the preregistered 24-month experiment. OKX liquidation/taker/ratio data are therefore marked `INSUFFICIENT` for this cycle rather than spliced into Binance history.

## Timestamp and causal availability

All timestamps are UTC. A 1h price record's `open_time` is the interval start and `close_time` is the interval end. Its decision time is the following hour boundary, after the candle is closed. Perpetual, mark, index, premium, and spot records are joined only on equal interval open times.

OI metrics are stock observations. `create_time` is both their source timestamp and the conservative historical availability timestamp. At a decision, only the last observation with `available_at <= T_decision` is eligible. OI age must be at most 65 minutes. `sum_open_interest` is base-asset quantity for the USD-margined symbol; `sum_open_interest_value` is quote-value and is the normalized primary series. Negative OI is impossible. Changes are derived from causally aligned stocks and are not flows.

Funding archive `calc_time` is the settled funding event time. `last_funding_rate` is actual settled funding, not a predicted next rate. It becomes visible at its timestamp and is backward-held for descriptive features for at most 12 hours. Interval changes use `funding_interval_hours`; no fixed 8h assumption is made. Realized funding cost sums only events strictly after fill and at or before exit. Using funding as a state feature does not itself add a cost; event settlement is counted once in trade P&L.

`perp_price`, `mark_price`, `index_price`, and `spot_price` are distinct close observations from the same one-hour interval. Derived fields are:

- `basis_absolute = perp_price - index_price`
- `basis_pct = perp_price / index_price - 1`
- `spot_basis_pct = perp_price / spot_price - 1`
- `annualized_basis` is not used for perpetuals because there is no maturity.

No nearest-neighbor join is allowed. Backward/as-of alignment uses availability time, exact price-bar joins use identical open times, and no interpolation uses a future observation. Each aligned input retains source timestamp, availability timestamp, age, and alignment method.

## Staleness and missing data

| Input | Maximum age at decision | Missing behavior |
|---|---:|---|
| closed price components | exact same 1h interval | `GAPPED`; no state |
| OI/positioning | 65 minutes | `STALE`; no OI-dependent signal |
| settled funding feature | 12 hours | `STALE`; no funding-dependent signal |
| liquidation/flow | unavailable in certified dataset | `INSUFFICIENT`; feature disabled |

Forward-filling is limited by these independent thresholds. Absence is never interpreted as zero. Long/short ratios and taker ratios are archived alongside OI but are descriptive secondary fields; they are not in primary candidate predicates.

## Integrity states

`VALID`, `STALE`, `GAPPED`, `INSUFFICIENT`, `CONFLICTING`, `PROVIDER_ERROR`, and `UNKNOWN` are distinct. Validation covers chronology, duplicates, future timestamps, gaps, NaN/inf, impossible negatives, units, symbols, schema changes, and discontinuities suggesting migration or parsing failure. Large but structurally valid market moves are retained. A required non-`VALID` input produces `NO_SIGNAL / DATA_INVALID`.

Liquidations, full trade flow/CVD, and order-book history are not certified in the primary dataset. They remain disabled rather than approximated from candles.
