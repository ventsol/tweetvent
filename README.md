# TweetVent v0.1.8

Watch Twitter accounts and forward tweets to Discord in real-time.

**No API key needed** — uses your browser cookies for instant fetching, with Nitter RSS as fallback.

## Features

- Direct Twitter fetching via Playwright (zero delay)
- Nitter RSS fallback if cookies expire
- Web dashboard with sidebar navigation
- Keyword include/exclude filters per account
- Custom embed colors per account
- Profile pictures in Discord embeds
- Image & video thumbnail support
- Gallery layout for multiple images
- Quote tweet & retweet support
- Tweet type labels (tweeted / replied / retweeted)
- External link previews in embeds
- 2-second spacing between tweets
- Per-account health status (working / rate-limited / no tweets)
- Cookie health indicator & update UI
- Discord webhook management in dashboard

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

## Files

| File | Purpose |
|---|---|
| `app.py` | Web dashboard server |
| `bot.py` | Standalone CLI bot |
| `bot_core.py` | Bot logic (used by dashboard) |
| `twitter_direct.py` | Twitter fetcher (Playwright + Nitter fallback) |
| `config.toml` | Your config (gitignored) |
| `config.example.toml` | Example config template |
| `templates/index.html` | Dashboard UI |
