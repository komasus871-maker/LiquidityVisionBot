# Liquidity Vision Intelligence v9.8.1

## Exchange Foundation: Binance USD-M Read-Only

This release introduces the first production exchange boundary without enabling LIVE execution.

### Included

- Typed `ExchangeAdapter` read-only contract.
- Normalized balances, positions, open orders, symbol rules, health and errors.
- Environment-backed `ExchangeRegistry`; secrets are never persisted.
- Working Binance USD-M Futures adapter for production or demo/testnet.
- Signed account requests for balance, position risk and open orders.
- Public server-time and exchange-info diagnostics.
- Telegram commands: `/exchanges`, `/exchange_balance`, `/exchange_positions`, `/exchange_orders`, `/exchange_symbol`.
- Mocked regression tests with no network dependency.

### Safety boundary

No order creation, modification, cancellation or leverage-changing method exists in the adapter contract. Paper Copy Trading remains the only execution path. This boundary must be preserved until a dedicated LIVE execution release adds idempotency, reconciliation, explicit arming and emergency controls.
