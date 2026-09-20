import os

from dotenv import load_dotenv

load_dotenv()

_legacy_bot_token = os.getenv("BOT_TOKEN", "").strip()
_explicit_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if _legacy_bot_token and _explicit_bot_token and _legacy_bot_token != _explicit_bot_token:
    raise RuntimeError("BOT_TOKEN and TELEGRAM_BOT_TOKEN disagree; refusing to change bot identity")
BOT_TOKEN = _explicit_bot_token or _legacy_bot_token

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN (or TELEGRAM_BOT_TOKEN) is not configured. Add the existing BotFather token "
        "to .env locally or to the deployment environment variables."
    )
