"""
TweetVent v0.1.5 — Web Dashboard
Run with: python app.py
Then open http://localhost:5000 in your browser.
"""

import threading
import tomllib
from pathlib import Path

import logging
from flask import Flask, jsonify, render_template, request

# Silence Flask's request logs (only show errors/warnings)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

from bot_core import DiscordBot, load_config, save_config

app = Flask(__name__)

# Create the bot instance (shared across requests)
bot = DiscordBot()
config_lock = threading.Lock()


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


@app.after_request
def add_no_cache(response):
    """Prevent browser caching of API responses."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/")
def index():
    """Serve the dashboard page."""
    return render_template("index.html")


@app.route("/status")
def status():
    """Return bot status as JSON for the UI to poll."""
    cfg = load_config()
    return jsonify({
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
        "auth_token": cfg.get("auth", {}).get("auth_token", ""),
        "ct0": cfg.get("auth", {}).get("ct0", ""),
        "cookie_healthy": _check_cookies(cfg),
        "direct_fetch_ok": bot.direct_fetch_ok,
    })


@app.route("/logs")
def logs():
    """Return recent logs."""
    n = request.args.get("n", 30, type=int)
    return jsonify(bot.get_logs(n))


@app.route("/recent")
def recent():
    """Return recent tweets."""
    n = request.args.get("n", 20, type=int)
    return jsonify([
        {"username": u, "url": url, "text": t[:100], "time": ts}
        for u, url, t, ts in bot.get_recent_tweets(n)
    ])


# ── Actions ─────────────────────────────────────────────────────────────────


@app.route("/start", methods=["POST"])
def start_bot():
    bot.start()
    return jsonify({"success": True, "status": bot.status})


@app.route("/stop", methods=["POST"])
def stop_bot():
    bot.stop()
    return jsonify({"success": True, "status": bot.status})


@app.route("/add_account", methods=["POST"])
def add_account():
    username = request.json.get("username", "").strip().lower()
    if not username:
        return jsonify({"success": False, "error": "No username provided"}), 400

    with config_lock:
        cfg = load_config()
        accounts = cfg["twitter"].setdefault("accounts", [])
        if username in [a.lower() for a in accounts]:
            return jsonify({"success": False, "error": f"@{username} is already being watched"}), 400
        accounts.append(username)
        save_config(cfg)

    bot._log(f"Added @{username} to watch list")
    return jsonify({"success": True, "accounts": cfg["twitter"]["accounts"]})


@app.route("/remove_account", methods=["POST"])
def remove_account():
    username = request.json.get("username", "").strip().lower()
    if not username:
        return jsonify({"success": False, "error": "No username provided"}), 400

    with config_lock:
        cfg = load_config()
        accounts = cfg["twitter"].get("accounts", [])
        cfg["twitter"]["accounts"] = [a for a in accounts if a.lower() != username]
        save_config(cfg)

    bot._log(f"Removed @{username} from watch list")
    return jsonify({"success": True, "accounts": cfg["twitter"]["accounts"]})


@app.route("/set_interval", methods=["POST"])
def set_interval():
    minutes = request.json.get("minutes", 5)
    minutes = max(1, min(60, int(minutes)))

    with config_lock:
        cfg = load_config()
        cfg["bot"]["poll_interval_minutes"] = minutes
        save_config(cfg)

    bot._log(f"Polling interval changed to {minutes} minutes")
    return jsonify({"success": True, "interval": minutes})


@app.route("/set_filters", methods=["POST"])
def set_filters():
    username = request.json.get("username", "").strip()
    include = request.json.get("include", "")
    exclude = request.json.get("exclude", "")

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
    return jsonify({"success": True, "filters": cfg.get("filters", {})})


@app.route("/set_color", methods=["POST"])
def set_color():
    username = request.json.get("username", "").strip()
    color = request.json.get("color", "").strip()

    with config_lock:
        cfg = load_config()
        if "colors" not in cfg:
            cfg["colors"] = {}
        if color:
            cfg["colors"][username] = color
        else:
            cfg["colors"].pop(username, None)
        save_config(cfg)

    bot._log(f"Color updated for @{username}")
    return jsonify({"success": True, "colors": cfg.get("colors", {})})


@app.route("/set_cookies", methods=["POST"])
def set_cookies():
    auth_token = request.json.get("auth_token", "").strip()
    ct0 = request.json.get("ct0", "").strip()

    with config_lock:
        cfg = load_config()
        if "auth" not in cfg:
            cfg["auth"] = {}
        cfg["auth"]["auth_token"] = auth_token
        cfg["auth"]["ct0"] = ct0
        save_config(cfg)

    healthy = _check_cookies(cfg)
    bot._log(f"Twitter cookies updated (valid: {healthy})")
    return jsonify({"success": True, "cookie_healthy": healthy})


@app.route("/set_webhook", methods=["POST"])
def set_webhook():
    url = request.json.get("url", "").strip()

    with config_lock:
        cfg = load_config()
        if "discord" not in cfg:
            cfg["discord"] = {}
        cfg["discord"]["webhook_url"] = url
        save_config(cfg)

    bot._log("Discord webhook updated")
    return jsonify({"success": True, "webhook_set": bool(url)})


@app.route("/set_nitter", methods=["POST"])
def set_nitter():
    instance = request.json.get("instance", "nitter.net").strip().lower()

    with config_lock:
        cfg = load_config()
        cfg["bot"]["nitter_instance"] = instance
        save_config(cfg)

    bot._log(f"Nitter instance changed to {instance}")
    return jsonify({"success": True, "instance": instance})


@app.route("/check_now", methods=["POST"])
def check_now():
    """Trigger an immediate check (runs in a background thread)."""
    def _check():
        bot._log("Manual check triggered via Check Now")
        bot.run_once()
    t = threading.Thread(target=_check, daemon=True)
    t.start()
    return jsonify({"success": True, "message": "Check started"})


# ── Main ────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    print("=" * 50)
    print("  TweetVent v0.1.5")
    print("  Web Dashboard")
    print("=" * 50)
    print()
    print("  Open http://localhost:5000 in your browser")
    print("  Press Ctrl+C to stop")
    print()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
