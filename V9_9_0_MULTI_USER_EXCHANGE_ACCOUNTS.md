# LiquidityVisionBot v9.9.0 — Multi-User Exchange Accounts

## What changed

v9.9.0 removes the shared-account execution assumption. Authenticated exchange reads and BingX demo execution now resolve credentials by the Telegram user who issued the command.

## Security model

- API credentials are encrypted with Fernet authenticated encryption before database storage.
- The encryption key exists only in `EXCHANGE_CREDENTIALS_MASTER_KEY` on the deployment host.
- Database records are isolated by the composite identity `(telegram_id, exchange)`.
- Credential messages are accepted only in a private bot chat and are deleted immediately after receipt.
- Failed authentication removes the just-created record, so invalid credentials are not retained.
- Live account connections remain locked unless `ALLOW_USER_LIVE_CONNECTIONS=true`.
- Users must grant Read + Trade only. Withdrawal permission must never be enabled.
- There is no fallback from a user command to the owner's global trading credentials.

## Commands

```text
/connect_exchange bingx demo API_KEY API_SECRET
/connect_exchange okx demo API_KEY API_SECRET PASSPHRASE
/my_exchanges
/disconnect_exchange bingx
```

After connection, these commands use only the sender's account:

```text
/exchange_balance bingx
/exchange_positions bingx
/exchange_orders bingx BTCUSDT
/exchange_account bingx
/exchange_preflight bingx BTCUSDT BUY 0.001 60000 3
/demo_order bingx BTCUSDT BUY LIMIT 0.001 60000 3 40000
/demo_status bingx BTCUSDT ORDER_ID
/demo_cancel bingx BTCUSDT ORDER_ID
```

## Render setup

Generate a key once and preserve it permanently:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the output as:

```env
EXCHANGE_CREDENTIALS_MASTER_KEY=<generated value>
ALLOW_USER_LIVE_CONNECTIONS=false
DEMO_EXECUTION_ENABLED=true
LIVE_EXECUTION_ENABLED=false
EXECUTION_REQUIRE_DEMO=true
```

Changing or losing the master key makes existing encrypted credentials unreadable. Users would then need to reconnect.

## Database

The startup schema creates `user_exchange_credentials` with encrypted key, encrypted secret, optional encrypted passphrase, testnet flag, status, timestamps, and a uniqueness constraint over user and exchange.
