# TweetVent v0.1.0

Watch Twitter accounts and forward tweets to Discord in real-time.

**No API key needed** — uses your browser cookies + a headless browser for instant fetching,
with Nitter RSS as fallback.

## Features

- Direct Twitter fetching via Playwright (zero delay)
- Nitter RSS fallback if cookies expire
- Web dashboard for managing accounts, filters, and settings
- Keyword include/exclude filters per account
- Custom embed colors per account
- Image support (regular images, video thumbnails)
- Quote tweet support

## Quick Start

1. Copy `config.example.toml` to `config.toml` and fill in your settings
2. Install requirements: `pip install -r requirements.txt`
3. Install Playwright browser: `python -m playwright install chromium`
4. Run the web dashboard: `python app.py`
5. Open http://localhost:5000

## Getting Twitter Cookies

1. Log into x.com in your browser
2. Press F12 -> Application tab -> Cookies -> x.com
3. Copy `auth_token` and `ct0` values into config or the web dashboard

## Files

| File | Purpose |
|---|---|
| `app.py` | Web dashboard server |
| `bot.py` | Standalone CLI bot |
| `bot_core.py` | Bot logic (used by dashboard) |
| `twitter_direct.py` | Direct Twitter fetcher via Playwright |
| `config.toml` | Your config (gitignored) |
| `config.example.toml` | Example config template |
| `templates/index.html` | Dashboard UI |
