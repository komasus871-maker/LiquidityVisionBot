"""Telegram Mini App authentication and a compact read-only terminal shell."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from html import escape
from urllib.parse import parse_qsl


@dataclass(frozen=True)
class WebAppIdentity:
    telegram_id: int
    username: str | None
    first_name: str | None
    auth_date: int


def validate_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 900,
                       now: int | None = None) -> WebAppIdentity:
    """Validate Telegram WebApp initData per Telegram's HMAC specification."""
    if not init_data:
        raise ValueError("missing initData")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received = values.pop("hash", "")
    if not received:
        raise ValueError("missing initData hash")
    data_check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise ValueError("invalid initData signature")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        telegram_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid initData identity") from exc
    current = int(time.time() if now is None else now)
    if auth_date > current + 30 or current - auth_date > max(30, int(max_age_seconds)):
        raise ValueError("expired initData")
    return WebAppIdentity(
        telegram_id=telegram_id, username=user.get("username"),
        first_name=user.get("first_name"), auth_date=auth_date,
    )


def terminal_html() -> str:
    """Self-contained terminal: no external assets and no raw-file access."""
    return """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Liquidity Vision Terminal</title><style>
:root{color-scheme:dark;--bg:#071018;--card:#101d29;--line:#203447;--text:#edf6ff;--muted:#8ea7bb;--a:#35d0ba}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif}
header{padding:18px 16px 8px}h1{font-size:20px;margin:0}small,.muted{color:var(--muted)}nav{display:flex;gap:8px;overflow:auto;padding:10px 16px}
button{border:1px solid var(--line);background:var(--card);color:var(--text);padding:9px 12px;border-radius:12px;white-space:nowrap}
button.active{border-color:var(--a);color:var(--a)}main{padding:8px 16px 30px}.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;white-space:pre-wrap;overflow-wrap:anywhere}.tag{color:var(--a);font-weight:700}
</style><script src="https://telegram.org/js/telegram-web-app.js"></script></head><body><header><h1>Liquidity Vision</h1><small>Read-only market terminal · no order controls</small></header>
<nav id="nav"></nav><main><div id="status" class="muted">Authenticating…</div><div id="cards" class="grid"></div></main>
<script>
const tg=window.Telegram&&window.Telegram.WebApp; if(tg){tg.ready();tg.expand()}
const init=tg?tg.initData:""; const pages=["overview","markets","scanner","signals","order-flow","derivatives","paper","portfolio","risk","alerts","shadow","system"];
const nav=document.getElementById("nav"),cards=document.getElementById("cards"),status=document.getElementById("status");
function clean(v){return v===null||v===undefined?"—":v} function draw(data){cards.innerHTML=""; const rows=data.items||[data]; rows.forEach(x=>{const d=document.createElement("div");d.className="card";d.textContent=Object.entries(x).filter(([k])=>!k.endsWith("_json")).map(([k,v])=>k.replaceAll("_"," ")+": "+clean(typeof v==="object"?JSON.stringify(v):v)).join("\n");cards.appendChild(d)});status.textContent=data.classification||"Bounded current state"}
async function load(page){[...nav.children].forEach(b=>b.classList.toggle("active",b.dataset.p===page));status.textContent="Loading…";cards.innerHTML="";if(!init){status.textContent="Open this Terminal from the Telegram button. Fallback: return to the bot and send /terminal.";return}try{const r=await fetch("/api/terminal/"+page,{headers:{"X-Telegram-Init-Data":init}});if(!r.ok)throw new Error("HTTP "+r.status);draw(await r.json())}catch(e){status.textContent="Terminal unavailable: "+e.message+". Return to Telegram and use the equivalent bot command."}}
pages.forEach(p=>{const b=document.createElement("button");b.textContent=p.replace("-"," ").toUpperCase();b.dataset.p=p;b.onclick=()=>load(p);nav.appendChild(b)});load("overview");
</script></body></html>"""
