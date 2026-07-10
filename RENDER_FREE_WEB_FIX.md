# Render Free Web Service fix

This release opens a lightweight HTTP server alongside Telegram long polling.
It binds to Render's `PORT` and exposes:

- `/` — plain-text online status;
- `/health` — JSON health response;
- `/healthz` — alias of `/health`.

Render configuration:

- Service type: **Web Service**
- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`
- Health check path: `/health`

Only one running service may use the same Telegram bot token. Stop old Render
services and local Termux processes before starting a new deployment.
