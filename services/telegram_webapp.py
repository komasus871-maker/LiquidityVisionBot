"""Telegram Mini App authentication and a compact read-only terminal shell."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from urllib.parse import parse_qsl


@dataclass(frozen=True)
class WebAppIdentity:
    telegram_id: int
    username: str | None
    first_name: str | None
    auth_date: int


class WebAppAuthError(ValueError):
    """Authentication failure with a user-safe reason code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def validate_init_data(init_data: str, bot_token: str, *, max_age_seconds: int = 900,
                       now: int | None = None) -> WebAppIdentity:
    """Validate Telegram WebApp initData per Telegram's HMAC specification."""
    if not init_data:
        raise WebAppAuthError("NO_INIT_DATA", "missing initData")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received = values.pop("hash", "")
    if not received:
        raise WebAppAuthError("AUTH_REJECTED", "missing initData hash")
    data_check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise WebAppAuthError("AUTH_REJECTED", "invalid initData signature")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        telegram_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WebAppAuthError("AUTH_REJECTED", "invalid initData identity") from exc
    current = int(time.time() if now is None else now)
    if auth_date > current + 30 or current - auth_date > max(30, int(max_age_seconds)):
        raise WebAppAuthError("AUTH_EXPIRED", "expired initData")
    return WebAppIdentity(
        telegram_id=telegram_id, username=user.get("username"),
        first_name=user.get("first_name"), auth_date=auth_date,
    )


def _b64encode(value: bytes) -> str:
    return urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_secret(bot_token: str) -> bytes:
    return hmac.new(
        b"LiquidityVisionTerminalSession-v1",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def issue_terminal_session(
    identity: WebAppIdentity,
    bot_token: str,
    *,
    ttl_seconds: int = 900,
    now: int | None = None,
) -> str:
    current = int(time.time() if now is None else now)
    payload = json.dumps({
        "v": 1,
        "telegram_id": identity.telegram_id,
        "username": identity.username,
        "first_name": identity.first_name,
        "auth_date": identity.auth_date,
        "issued_at": current,
        "expires_at": current + max(60, min(3600, int(ttl_seconds))),
    }, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_session_secret(bot_token), payload, hashlib.sha256).digest()
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def validate_terminal_session(
    token: str,
    bot_token: str,
    *,
    now: int | None = None,
) -> WebAppIdentity:
    try:
        payload_part, signature_part = token.split(".", 1)
        payload = _b64decode(payload_part)
        received = _b64decode(signature_part)
        expected = hmac.new(_session_secret(bot_token), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, received):
            raise WebAppAuthError("AUTH_REJECTED", "invalid terminal session")
        values = json.loads(payload)
        if int(values.get("v")) != 1:
            raise WebAppAuthError("AUTH_REJECTED", "unsupported terminal session")
        current = int(time.time() if now is None else now)
        if current > int(values["expires_at"]):
            raise WebAppAuthError("AUTH_EXPIRED", "expired terminal session")
        if int(values["issued_at"]) > current + 30:
            raise WebAppAuthError("AUTH_REJECTED", "invalid terminal session time")
        return WebAppIdentity(
            telegram_id=int(values["telegram_id"]),
            username=values.get("username"),
            first_name=values.get("first_name"),
            auth_date=int(values["auth_date"]),
        )
    except WebAppAuthError:
        raise
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise WebAppAuthError("AUTH_REJECTED", "invalid terminal session") from exc


def terminal_html() -> str:
    """Self-contained terminal with bounded Telegram authentication states."""
    # Raw string is intentional: JavaScript escape sequences must not become
    # literal line breaks inside quoted expressions.
    return r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Liquidity Vision Terminal</title><style>
:root{color-scheme:dark;--bg:#071018;--card:#101d29;--line:#203447;--text:#edf6ff;--muted:#8ea7bb;--a:#35d0ba;--bad:#ff7c8a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,sans-serif}
header{padding:calc(18px + env(safe-area-inset-top)) 16px 8px}h1{font-size:20px;margin:0}small,.muted{color:var(--muted)}
nav{display:flex;gap:8px;overflow:auto;padding:10px 16px;scrollbar-width:none}nav:empty{display:none}
button{border:1px solid var(--line);background:var(--card);color:var(--text);padding:10px 13px;border-radius:12px;white-space:nowrap}
button.active{border-color:var(--a);color:var(--a)}button:disabled{opacity:.5}main{padding:8px 16px 30px}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;white-space:pre-wrap;overflow-wrap:anywhere}
.error{color:var(--bad)}.actions{display:flex;gap:10px;margin-top:14px}.hidden{display:none}
</style></head><body><header><h1>Liquidity Vision</h1><small>Read-only market terminal · no order controls</small></header>
<nav id="nav"></nav><main><div id="status" class="muted">Authenticating…</div><div id="actions" class="actions hidden"></div><div id="cards" class="grid"></div></main>
<script>
"use strict";
const pages=["overview","markets","scanner","signals","order-flow","derivatives","paper","portfolio","risk","alerts","shadow","economics","system"];
const reasons={NO_INIT_DATA:"Open this Terminal from its Telegram button.",AUTH_REJECTED:"Telegram could not verify this session.",AUTH_EXPIRED:"This Telegram session has expired.",AUTH_TIMEOUT:"Telegram authentication timed out.",API_UNAVAILABLE:"The Terminal API is temporarily unavailable.",BOOTSTRAP_FAILED:"Terminal data could not be loaded."};
const nav=document.getElementById("nav"),cards=document.getElementById("cards"),status=document.getElementById("status"),actions=document.getElementById("actions");
let sessionToken="",tg=null,authGeneration=0,bridgeSettled=false;
function event(name,detail){console.info(name,detail||"")}
function clean(v){return v===null||v===undefined?"—":v}
function setActions(show){actions.innerHTML="";actions.classList.toggle("hidden",!show);if(!show)return;const retry=document.createElement("button");retry.textContent="Retry";retry.onclick=authenticate;const close=document.createElement("button");close.textContent="Close / Back";close.onclick=()=>{if(tg&&typeof tg.close==="function")tg.close();else history.back()};actions.append(retry,close)}
function fail(code){const safe=reasons[code]?code:"API_UNAVAILABLE";status.className="error";status.textContent="Authentication failed\nReason: "+safe+"\n"+reasons[safe];cards.innerHTML="";setActions(true);event("TERMINAL_AUTH_FAILED",safe)}
function abortable(ms){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),ms);return{signal:controller.signal,done:()=>clearTimeout(timer)}}
async function request(url,options,timeoutMs){const guard=abortable(timeoutMs);try{return await fetch(url,{...options,signal:guard.signal})}finally{guard.done()}}
function draw(data){cards.innerHTML="";if(data.scanner_health){const health=document.createElement("div");health.className="card";health.textContent="SCANNER DATA PLANE\n"+Object.entries(data.scanner_health).filter(([k])=>["scanner_status","scanner_status_reason","monitored_symbols","universe_target","baseline_ready_symbols","active_episodes","new_anomalies_1h","escalations_1h","venues_healthy","venues_degraded","broad_radar_age_seconds","cycle_duration_seconds"].includes(k)).map(([k,v])=>k.replace(/_/g," ")+": "+clean(v)).join("\n");cards.appendChild(health)}if(data.available_views){const chooser=document.createElement("div");chooser.className="card";data.available_views.forEach(mode=>{const button=document.createElement("button");button.textContent=mode.replace(/_/g," ");button.classList.toggle("active",mode===data.selected_view);button.onclick=()=>load("scanner?view="+encodeURIComponent(mode));chooser.appendChild(button)});cards.appendChild(chooser)}const rows=data.items||[data];if(!rows.length){const empty=document.createElement("div");empty.className="card muted";empty.textContent="No qualifying records. Scanner diagnostics above distinguish quiet market, warmup, and data-plane failure.";cards.appendChild(empty)}rows.forEach(x=>{const d=document.createElement("div");d.className="card";d.textContent=Object.entries(x).filter(([k])=>!k.endsWith("_json")).map(([k,v])=>k.replace(/_/g," ")+": "+clean(typeof v==="object"?JSON.stringify(v):v)).join("\n");cards.appendChild(d)});status.className="muted";status.textContent="Terminal loaded · "+(data.classification||"current state");setActions(false)}
async function load(page,bootstrap=false){if(!sessionToken){fail("AUTH_REJECTED");return}const generation=authGeneration,basePage=page.split("?",1)[0];[...nav.children].forEach(b=>b.classList.toggle("active",b.dataset.p===basePage));status.className="muted";status.textContent=bootstrap?"Loading Terminal…":"Refreshing…";cards.innerHTML="";if(bootstrap)event("TERMINAL_BOOTSTRAP_STARTED");try{const response=await request("/api/terminal/"+page,{headers:{Authorization:"Bearer "+sessionToken}},10000);let data={};try{data=await response.json()}catch(_error){}if(generation!==authGeneration)return;if(!response.ok){if(response.status===401||response.status===403)throw Object.assign(new Error("AUTH_REJECTED"),{code:data.code||"AUTH_REJECTED"});throw Object.assign(new Error("BOOTSTRAP_FAILED"),{code:"BOOTSTRAP_FAILED"})}draw(data);if(bootstrap)event("TERMINAL_BOOTSTRAP_COMPLETE")}catch(error){if(generation!==authGeneration)return;const code=error.name==="AbortError"?"API_UNAVAILABLE":(error.code||"BOOTSTRAP_FAILED");fail(code)}}
async function authenticate(){authGeneration+=1;const generation=authGeneration;sessionToken="";nav.innerHTML="";cards.innerHTML="";setActions(false);status.className="muted";status.textContent="Authenticating…";event("TERMINAL_AUTH_START");tg=window.Telegram&&window.Telegram.WebApp;if(!tg){fail("NO_INIT_DATA");return}try{tg.ready();tg.expand();event("TELEGRAM_JS_READY")}catch(_error){fail("API_UNAVAILABLE");return}const initData=String(tg.initData||"");if(!initData){fail("NO_INIT_DATA");return}event("INIT_DATA_PRESENT");try{event("AUTH_REQUEST_SENT");const response=await request("/api/terminal/auth",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({init_data:initData,js_ready:true})},8000);let data={};try{data=await response.json()}catch(_error){}event("AUTH_RESPONSE",response.status);if(generation!==authGeneration)return;if(!response.ok||!data.session_token){fail(data.code||"AUTH_REJECTED");return}sessionToken=data.session_token;event("AUTH_SUCCESS");pages.forEach(page=>{const button=document.createElement("button");button.textContent=page.replace(/-/g," ").toUpperCase();button.dataset.p=page;button.onclick=()=>load(page);nav.appendChild(button)});await load("overview",true)}catch(error){if(generation!==authGeneration)return;fail(error.name==="AbortError"?"AUTH_TIMEOUT":"API_UNAVAILABLE")}}
function loadTelegramBridge(){const script=document.createElement("script");script.src="https://telegram.org/js/telegram-web-app.js";script.async=true;script.onload=()=>{if(bridgeSettled)return;bridgeSettled=true;authenticate()};script.onerror=()=>{if(bridgeSettled)return;bridgeSettled=true;fail("API_UNAVAILABLE")};document.head.appendChild(script);setTimeout(()=>{if(bridgeSettled)return;bridgeSettled=true;if(window.Telegram&&window.Telegram.WebApp)authenticate();else fail("AUTH_TIMEOUT")},5000)}
loadTelegramBridge();
</script></body></html>"""
