# TweetVent v0.2.2

Watch Twitter accounts and forward tweets to Discord in real-time.

**No API key needed** — uses your browser cookies for instant fetching, with Nitter RSS as fallback.

## Features

- **Direct Twitter fetching** via Playwright (zero delay)
- **Nitter RSS fallback** if cookies expire
- **Glassmorphism UI** with dark/light mode toggle
- **Per-account pause/resume** — temporarily stop watching accounts
- **Multi-webhook support** — route accounts to different Discord channels
- **Keyword filters** — include/exclude per account
- **Custom embed colors** per account
- **Profile pictures** in Discord embeds
- **Image & video thumbnail** support with gallery layout
- **Tweet type labels** — tweeted / replied / retweeted / quote tweeted
- **Retweet/quote formatting** with clear separators
- **External link previews** in embeds
- **Account health monitoring** — working / rate-limited / paused
- **Cookie health indicator** with update UI

## Quick Start

```bash
# Install requirements
pip install -r requirements.txt

# Install Playwright browser
python -m playwright install chromium

# Copy and configure
cp config.example.toml config.toml

# Run the web dashboard
python app.py
```

Open **http://localhost:5000** in your browser.

## Getting Twitter Cookies

1. Log into **x.com** in your browser
2. Press **F12** → Application tab → Cookies → x.com
3. Copy `auth_token` and `ct0` values into Settings page in the dashboard

## Screenshots

Dashboard features:
- Sidebar navigation (Dashboard, Accounts, Settings, Logs, Recent)
- Live status grid with bot health, account stats, cookie status
- Account management with inline filters and webhook configuration
- Dark/Light mode toggle at the bottom of the sidebar

## Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI web dashboard server |
| `bot.py` | Standalone CLI bot |
| `bot_core.py` | Bot logic (used by dashboard) |
| `twitter_direct.py` | Twitter fetcher (Playwright + Nitter fallback) |
| `config.toml` | Your config (gitignored) |
| `config.example.toml` | Example config template |
| `templates/index.html` | Dashboard UI |
