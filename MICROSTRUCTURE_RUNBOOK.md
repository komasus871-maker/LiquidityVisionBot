# Bounded Microstructure Observer

## Data source and scope

The observer uses BingX public perpetual-futures market endpoints documented by BingX:

- `/openApi/swap/v2/quote/depth`
- `/openApi/swap/v2/quote/premiumIndex`
- `/openApi/swap/v2/quote/openInterest`

No credential or order endpoint is required. The observer constructs a credential-empty production-public client, invokes only the three public market-data methods above, and has no call path to place, cancel or modify an order. This market-data choice is independent from `BINGX_DEMO`, account certification and every LIVE gate.

## Configuration

```text
MICROSTRUCTURE_COLLECTION_ENABLED=true
MICROSTRUCTURE_INTERVAL_SECONDS=60
MICROSTRUCTURE_MAX_SYMBOLS=8
MICROSTRUCTURE_SAMPLES_PER_SYMBOL=5
MICROSTRUCTURE_SAMPLE_SPACING_MS=400
MICROSTRUCTURE_MAX_LEVELS=50
```

Symbols are limited to recent open signals, pending candidates, unpromoted observations and watchlists. Sampling is sequential and bounded. Each cycle stores only an aggregate; in-memory books are capped and discarded on restart. The distributed lease is renewed per symbol and sized for bounded timeout latency. A restart collects a fresh burst and creates a checksum-protected aggregate.

## Interpretation

Wall states are `STATIC_WALL`, `PERSISTENT_WALL`, `PULLED_WALL`, `REPLENISHING_WALL` and `SWEPT_WALL`. A possible spoof requires a large wall to disappear without observed price interaction. Possible absorption requires multiple samples, meaningful imbalance and little price progress.

These are observable-behavior labels. They never identify a trader, institution or “whale.” One large resting order is not confirmation. Absorption remains `UNCONFIRMED` unless aggressive executed-flow fields are actually supplied; public depth alone cannot prove absorption.

## Operations

Use `/orderbook BTCUSDT`.

PASS:

- status is `AVAILABLE`;
- at least three samples are present;
- `stale` is false;
- interaction quality and depth bands are finite;
- raw bids/asks are absent from `aggregate_json`;
- normal signal, copy and LIVE workers remain healthy if BingX public data fails.

FAIL:

- repeated normalized request/response errors;
- stale aggregates beyond two observer intervals;
- raw book arrays in persistence;
- unbounded symbols/samples;
- observer failure propagating into signal analysis or execution.

Disable only this path with `MICROSTRUCTURE_COLLECTION_ENABLED=false`. Existing intelligence snapshots, analysis, PAPER copy and LIVE gates continue independently.
