# v7.3 — Trade DNA

## Added
- Canonical `TradeDNA` v2 snapshot with stable fingerprint and normalized market, execution, risk and indicator features.
- `SimilarityEngineV2` with weighted mixed-type comparison, matching/difference reasons, expected R, best/worst comparable trades and reliability.
- Persistent `trade_memories` and automatic deterministic post-trade lessons after lifecycle closure.
- DNA-aware Explain Pro and Similarity Report output.
- Dataset-ready DNA fields on every newly recorded signal.
- Learning progress service exposing DNA library, comparable trades, completion progress and reliability.

## Database
Migrations are additive and idempotent. New signal columns: `trade_dna_json`, `dna_fingerprint`, `memory_created_at`. New table: `trade_memories`.

## Compatibility
Existing `features_json` rows are converted to TradeDNA on read, so historical records remain usable without a destructive migration.
