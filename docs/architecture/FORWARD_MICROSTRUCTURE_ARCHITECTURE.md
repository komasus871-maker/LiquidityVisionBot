# Forward Microstructure Architecture

## Safety boundary

The forward lab is a standalone public-data research subsystem. It reads no private credentials, imports no LIVE/PAPER/copy execution adapter, exposes no place/cancel order method, and writes `execution_authority=false` into feature snapshots, Shadow decisions, and status output. Production defaults remain disabled and unchanged.

## Event contract

Every `RawMarketEvent` preserves venue, market, normalized symbol, instrument type, event type, exchange timestamp, local receive timestamp, sequence identifiers when supplied, price/quantity/side, maker/aggressor semantics, connection ID, full raw JSON, raw SHA-256, semantic ID, schema version, and integrity status.

The SQLite ledger uses WAL plus full synchronous durability. Raw payloads use lossless zlib-compressed canonical JSON (`forward-microstructure-event-v2`); mixed v1 text and v2 compressed rows remain replayable. Raw events, features, decisions, labels, and checkpoints are append-only; database triggers reject UPDATE and DELETE. Duplicate payloads are retained and linked to their first ingest rather than silently discarded. Only the first semantic event is admitted to causal state. Routine valid-book checkpoints are throttled to 30 seconds per instrument while gap/resync checkpoints remain immediate.

## Clock and recovery

Exchange time is descriptive event time. Receive time is the decision-time availability clock. Out-of-order arrivals are preserved and counted. Sequence discontinuities invalidate the book, write an append-only checkpoint, and request resynchronization. Binance reloads a REST snapshot while retaining the public stream; OKX reconnects to obtain a fresh channel snapshot. BingX uses bounded snapshots, so it never claims incremental sequence integrity.

Every reconnect receives a new connection ID. Public connectors back off from one to thirty seconds and retain raw gaps rather than silently bridging them. Binance connections are designed to reconnect before/after the venue's finite connection lifetime through the same recovery loop.

## L2 state

Binance applies the documented REST-snapshot bridge plus subsequent `U/u/pu` delta sequence contract. OKX applies strict `seqId/prevSeqId` continuity. The legacy signed CRC32 implementation remains testable for old captured payloads, but current JSON-book checksum zero is ignored because OKX deprecated it on 2026-06-23. A crossed book, empty side, or sequence gap produces an invalid state and resync request. BingX top-20 snapshots are usable for bounded current depth but are labelled `SNAPSHOT_ONLY`.

Derived book state includes best bid/ask, midpoint, spread/bps, top-level and top-N quantities, notional depth imbalance, microprice, microprice deviation, and liquidity within 10 bps.

## Causal features and Shadow research

Trade flow uses true aggressor semantics on 1s, 5s, 15s, 30s, 1m, and 5m windows. Features include notional delta, normalized delta, CVD, trade-count imbalance, average trade size, large-trade delta, concentration, and burst intensity. Liquidation windows are 1m and 5m. OI, funding, mark/index, basis, event age, receive latency, and cross-venue midpoint/flow dispersion remain venue-explicit.

The feature engine produces descriptive states only. The Shadow engine records frozen-candidate observations and conservative entry/exit fill simulations under 50ms, 250ms, and 1,000ms latency. Outcome labels and scenario-specific net PnL live in a separate table and can never re-enter feature construction. Receive-order replay rebuilds feature and decision identities from first-seen raw events without mutating the source ledger.

## Venue capabilities

| Venue | Trades | L2 | Liquidations | OI | Funding/mark/index |
|---|---|---|---|---|---|
| Binance | public aggressor-labelled | incremental + REST snapshot | public force order | public REST poll | public WebSocket |
| OKX | public taker-side | incremental `seqId/prevSeqId` | public liquidation-orders | public WebSocket | public WebSocket |
| BingX | public aggressor-labelled | top-20 snapshots | unavailable | public REST poll | public WebSocket/REST |

Capability absence fails closed for dependent candidates.
