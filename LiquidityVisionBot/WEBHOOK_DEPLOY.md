# Render webhook deployment

Use a **Web Service**.

- Build: `pip install -r requirements.txt`
- Start: `python bot.py`
- `BOT_MODE=webhook`
- `WEBHOOK_BASE_URL=https://liquidityvisionbot-1.onrender.com`
- `BOT_TOKEN=<secret>`

Expected logs:

```text
Liquidity Vision starting in webhook mode
HTTP server listening on http://0.0.0.0:10000
Webhook active: https://liquidityvisionbot-1.onrender.com/telegram/webhook
```

There must be no `start_polling`, `Failed to fetch updates`, or
`TelegramConflictError` messages in webhook mode.

Health check:

`https://liquidityvisionbot-1.onrender.com/health`
