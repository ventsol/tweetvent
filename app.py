"""
TweetVent v0.1.10 — Web Dashboard (FastAPI)
Run with: python app.py
Then open http://localhost:5000 in your browser.
"""

import threading

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pathlib import Path
from starlette.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from bot_core import DiscordBot, load_config, save_config

# Create FastAPI app
app = FastAPI(title="TweetVent", version="0.1.7")

# Template setup
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Create the bot instance (shared across requests)
bot = DiscordBot()
config_lock = threading.Lock()


# ── Middleware: No-cache headers ─────────────────────────────────────────────

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheMiddleware)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _check_cookies(cfg):
    """Test if Twitter auth cookies are still valid."""
    auth = cfg.get("auth", {})
    token = auth.get("auth_token", "")
    ct0 = auth.get("ct0", "")
    if not token or not ct0:
        return False
    try:
        import requests
        r = requests.get("https://x.com/", cookies={"auth_token": token, "ct0": ct0},
            headers={"User-Agent": "Mozilla/5.0", "x-csrf-token": ct0}, timeout=10)
        return r.status_code == 200 and len(r.text) > 1000
    except Exception:
        return False


# ── Web Routes ──────────────────────────────────────────────────────────────


@app.get("/")
async def index(request: Request):
    """Serve the dashboard page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/status")
async def status():
    """Return bot status as JSON for the UI to poll."""
    cfg = load_config()
    return JSONResponse({
        "status": bot.status,
        "is_running": bot.is_running,
        "last_check": bot.last_check_time,
        "last_result": bot.last_check_result,
        "account_stats": bot.account_stats,
        "accounts": cfg["twitter"].get("accounts", []),
        "colors": cfg.get("colors", {}),
        "filters": cfg.get("filters", {}),
        "poll_interval": cfg["bot"].get("poll_interval_minutes", 5),
        "nitter_instance": cfg["bot"].get("nitter_instance", "nitter.net"),
        "webhook_set": bool(cfg["discord"].get("webhook_url", "")),
        "per_account_webhooks": cfg.get("discord", {}).get("webhooks", {}),
        "auth_token": cfg.get("auth", {}).get("auth_token", ""),
        "ct0": cfg.get("auth", {}).get("ct0", ""),
        "cookie_healthy": _check_cookies(cfg),
        "direct_fetch_ok": bot.direct_fetch_ok,
        "account_health": bot.account_health,
        "paused": cfg.get("paused", []),
    })


@app.get("/logs")
async def logs(n: int = 30):
    """Return recent logs."""
    return JSONResponse(bot.get_logs(n))


@app.get("/recent")
async def recent():
    """Return recent tweets posted."""
    return JSONResponse(bot.get_recent_tweets())


@app.post("/clear_recent")
async def clear_recent():
    """Clear the recent tweets list."""
    bot.clear_recent()
    return JSONResponse({"success": True})


@app.post("/start")
async def start():
    bot.start()
    return JSONResponse({"success": True})


@app.post("/stop")
async def stop():
    bot.stop()
    return JSONResponse({"success": True})


@app.post("/check_now")
async def check_now():
    """Trigger an immediate check."""
    def _check():
        bot._log("Manual check triggered via Check Now")
        bot.run_once()
    t = threading.Thread(target=_check, daemon=True)
    t.start()
    return JSONResponse({"success": True, "message": "Check started"})


@app.post("/add_account")
async def add_account(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    if not username:
        return JSONResponse({"success": False, "error": "Username is required"})

    with config_lock:
        cfg = load_config()
        accounts = cfg["twitter"].get("accounts", [])
        if any(a.lower() == username.lower() for a in accounts):
            return JSONResponse({"success": False, "error": "Account already added"})
        accounts.append(username)
        cfg["twitter"]["accounts"] = accounts
        save_config(cfg)

    # Set initial state to latest tweet so old tweets don't get posted
    try:
        auth_token = cfg.get("auth", {}).get("auth_token", "")
        ct0 = cfg.get("auth", {}).get("ct0", "")
        from twitter_direct import fetch_tweets_direct
        tweets = fetch_tweets_direct(username, auth_token, ct0)
        if tweets:
            import re
            from urllib.parse import urlparse
            latest_id = None
            for t in tweets:
                path = urlparse(t.link).path
                parts = path.split("/")
                if "status" in parts:
                    idx = parts.index("status") + 1
                    tid = parts[idx].split("#")[0]
                    if latest_id is None or int(tid) > int(latest_id):
                        latest_id = tid
            if latest_id:
                from bot_core import STATE_PATH
                import json
                state = {}
                if STATE_PATH.exists():
                    with open(STATE_PATH) as f:
                        data = json.load(f)
                        state = data.get("last_tweet_id", {})
                        if isinstance(state, str):
                            state = {}
                state[username] = latest_id
                with open(STATE_PATH, "w") as f:
                    json.dump({"last_tweet_id": state}, f)
                bot._log(f"@{username}: Initial state set to tweet {latest_id}")
    except Exception as e:
        bot._log(f"@{username}: Could not set initial state ({e}) - may post old tweets on first check")

    bot._log(f"Added @{username}")
    return JSONResponse({"success": True, "accounts": accounts})


@app.post("/remove_account")
async def remove_account(request: Request):
    data = await request.json()
    username = data.get("username", "").strip().lower()

    with config_lock:
        cfg = load_config()
        accounts = cfg["twitter"].get("accounts", [])
        cfg["twitter"]["accounts"] = [a for a in accounts if a.lower() != username]
        save_config(cfg)

    bot._log(f"Removed @{username}")
    return JSONResponse({"success": True, "accounts": cfg["twitter"]["accounts"]})


@app.post("/set_color")
async def set_color(request: Request):
    data = await request.json()
    username = data.get("username", "")
    color = data.get("color", "")

    with config_lock:
        cfg = load_config()
        if "colors" not in cfg:
            cfg["colors"] = {}
        cfg["colors"][username] = color
        save_config(cfg)

    bot._log(f"Color updated for @{username}")
    return JSONResponse({"success": True, "colors": cfg.get("colors", {})})


@app.post("/set_filters")
async def set_filters(request: Request):
    data = await request.json()
    username = data.get("username", "")
    include = data.get("include", "")
    exclude = data.get("exclude", "")

    with config_lock:
        cfg = load_config()
        if "filters" not in cfg:
            cfg["filters"] = {"include": {}, "exclude": {}}
        if "include" not in cfg["filters"]:
            cfg["filters"]["include"] = {}
        if "exclude" not in cfg["filters"]:
            cfg["filters"]["exclude"] = {}
        cfg["filters"]["include"][username] = include
        cfg["filters"]["exclude"][username] = exclude
        save_config(cfg)

    bot._log(f"Filters updated for @{username}")
    return JSONResponse({"success": True, "filters": cfg.get("filters", {})})


@app.post("/toggle_pause")
async def toggle_pause(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()

    with config_lock:
        cfg = load_config()
        paused = cfg.get("paused", [])
        if username in paused:
            paused.remove(username)
            bot._log(f"@{username}: Resumed")
        else:
            paused.append(username)
            bot._log(f"@{username}: Paused")
        cfg["paused"] = paused
        save_config(cfg)

    return JSONResponse({"success": True, "paused": paused, "is_paused": username in paused})


@app.post("/set_interval")
async def set_interval(request: Request):
    data = await request.json()
    minutes = float(data.get("minutes", 5))

    with config_lock:
        cfg = load_config()
        cfg["bot"]["poll_interval_minutes"] = minutes
        save_config(cfg)

    bot._log(f"Polling interval changed to {minutes} minutes")
    return JSONResponse({"success": True})


@app.post("/set_nitter")
async def set_nitter(request: Request):
    data = await request.json()
    instance = data.get("instance", "nitter.net").strip().lower()

    with config_lock:
        cfg = load_config()
        cfg["bot"]["nitter_instance"] = instance
        save_config(cfg)

    bot._log(f"Nitter instance changed to {instance}")
    return JSONResponse({"success": True, "instance": instance})


@app.post("/set_cookies")
async def set_cookies(request: Request):
    data = await request.json()
    auth_token = data.get("auth_token", "").strip()
    ct0 = data.get("ct0", "").strip()

    with config_lock:
        cfg = load_config()
        if "auth" not in cfg:
            cfg["auth"] = {}
        cfg["auth"]["auth_token"] = auth_token
        cfg["auth"]["ct0"] = ct0
        save_config(cfg)

    healthy = _check_cookies(cfg)
    bot._log(f"Twitter cookies updated (valid: {healthy})")
    return JSONResponse({"success": True, "cookie_healthy": healthy})


@app.post("/set_webhook")
async def set_webhook(request: Request):
    data = await request.json()
    url = data.get("url", "").strip()

    with config_lock:
        cfg = load_config()
        if "discord" not in cfg:
            cfg["discord"] = {}
        cfg["discord"]["webhook_url"] = url
        save_config(cfg)

    bot._log("Discord webhook updated")
    return JSONResponse({"success": True, "webhook_set": bool(url)})


@app.post("/set_account_webhook")
async def set_account_webhook(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    url = data.get("url", "").strip()

    with config_lock:
        cfg = load_config()
        if "discord" not in cfg:
            cfg["discord"] = {}
        if "webhooks" not in cfg["discord"]:
            cfg["discord"]["webhooks"] = {}
        if url:
            cfg["discord"]["webhooks"][username] = url
        else:
            cfg["discord"]["webhooks"].pop(username, None)
        save_config(cfg)

    bot._log(f"Webhook set for @{username}")
    return JSONResponse({"success": True, "webhooks": cfg["discord"].get("webhooks", {})})


# ── Main ────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  TweetVent v0.1.10")
    print("  Web Dashboard (FastAPI)")
    print("=" * 50)
    print()
    print("  Open http://localhost:5000 in your browser")
    print("  Press Ctrl+C to stop")
    print()
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="error")
