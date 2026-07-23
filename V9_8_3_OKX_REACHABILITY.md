# v9.8.3 — Exchange Reachability: OKX Read-Only

This release adds OKX V5 as the next independent read-only exchange transport.

## Commands

- `/exchanges`
- `/exchange_symbol okx BTCUSDT`
- `/exchange_symbol okx BTC-USDT-SWAP`
- `/exchange_balance okx`
- `/exchange_positions okx`
- `/exchange_orders okx BTCUSDT`

## Safety

The adapter has no write methods. Demo mode adds `x-simulated-trading: 1`. Secrets and passphrases are loaded only from environment variables.
