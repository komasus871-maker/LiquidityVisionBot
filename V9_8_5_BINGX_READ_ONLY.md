# v9.8.5 — BingX Read-Only Reachability

Adds BingX USDT-M perpetuals to the exchange registry without any order-write capability.

## Scope

- public server-time health check and Render reachability diagnostics;
- public contract/execution rules with BTCUSDT -> BTC-USDT normalization;
- authenticated read-only balances, positions, and open orders;
- bounded retries, split timeouts, safe non-JSON diagnostics, and symbol-rule caching;
- API secrets remain environment-only.

## Render smoke test

```
/exchanges
/exchange_symbol bingx BTCUSDT
/exchange_symbol bingx BTC-USDT
/exchange_balance bingx
```
