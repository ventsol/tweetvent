"""
TweetVent v0.2.1
Watches Twitter accounts and forwards new tweets to Discord.
No API key required.
"""

import json
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

# ── Config ──────────────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "config.toml"
STATE_PATH = Path(__file__).parent / "last_tweet.json"


def load_config():
    """Load and validate config.toml."""
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    if not cfg["twitter"].get("accounts"):
        raise ValueError("No Twitter accounts configured in config.toml!")
    if not cfg["discord"].get("webhook_url"):
        raise ValueError("Discord webhook URL is not set!")
    return cfg


# ── Twitter (via Nitter RSS) ────────────────────────────────────────────────


def fetch_rss_feed(username, instance="nitter.net"):
    """Fetch the RSS feed for a Twitter user from a Nitter instance."""
    url = f"https://{instance}/{username}/rss?_t={int(time.time())}"
    feed = feedparser.parse(url)

    if feed.bozo and not feed.entries:
        raise ConnectionError(
            f"Failed to fetch RSS feed from {url}. "
            f"The Nitter instance '{instance}' may be down."
        )
    return feed


def parse_tweet_id_from_link(link):
    """Extract the numeric tweet ID from a Nitter tweet URL."""
    path = urlparse(link).path.rstrip("/")
    parts = path.split("/")
    if "status" in parts:
        idx = parts.index("status")
        if idx + 1 < len(parts):
            return parts[idx + 1].split("#")[0]
    return None


def get_own_tweets(feed, username):
    """Filter feed items to only the user's own tweets (exclude retweets of others)."""
    own = []
    for entry in feed.entries:
        author = entry.get("author", "")
        if author.startswith("@"):
            author = author[1:]
        if author.lower() == username.lower():
            own.append(entry)
    return own


# ── Discord ─────────────────────────────────────────────────────────────────


def to_twitter_cdn_url(nitter_url):
    """Convert a Nitter /pic/ URL to the original Twitter CDN URL."""
    if '/pic/' not in nitter_url:
        return nitter_url
    from urllib.parse import unquote
    decoded = unquote(nitter_url)
    path = decoded.split('/pic/', 1)[1]
    if path.startswith('pbs.twimg.com'):
        return 'https://' + path
    return f'https://pbs.twimg.com/{path}'


def extract_images(summary_html):
    """Extract image URLs from the Nitter RSS summary HTML."""
    if not summary_html:
        return []
    soup = BeautifulSoup(summary_html, 'html.parser')
    images = []
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if src and src.startswith('https://'):
            src = to_twitter_cdn_url(src)
            images.append(src)
    return images


def extract_links(summary_html):
    """Extract external links from Nitter RSS summary HTML."""
    if not summary_html:
        return []
    soup = BeautifulSoup(summary_html, 'html.parser')
    links = []
    for a in soup.find_all('a'):
        href = a.get('href', '')
        if href and href.startswith('http') and not any(d in href for d in ['nitter.net', 'twitter.com', 'x.com', '/status/', '/search']):
            links.append(href)
    return links[:3]


def has_video(summary_html):
    """Check if summary contains video content."""
    return 'amplify_video' in summary_html or 'tweet_video_thumb' in summary_html


def hex_to_int(hex_color):
    """Convert hex color string to Discord integer."""
    if not hex_color or not isinstance(hex_color, str):
        return 1942002
    hex_color = hex_color.lstrip('#')
    try:
        return int(hex_color, 16)
    except (ValueError, TypeError):
        return 1942002


def post_to_discord(webhook_url, entry, username, color=None, tweet_type="tweet"):
    """Send a tweet to a Discord webhook channel."""
    tweet_id = parse_tweet_id_from_link(entry.link)
    if not tweet_id:
        return False

    tweet_url = f"https://x.com/{username}/status/{tweet_id}"
    pubdate = entry.get("published", "unknown")
    summary = entry.get("summary", "")

    # Use summary (strip HTML) for full tweet text -- title is truncated by Nitter
    tweet_text = BeautifulSoup(summary, 'html.parser').get_text(strip=True) if summary else ""
    if not tweet_text:
        tweet_text = entry.get("title", "")

    # Discord embed description limit is 4096 chars
    if len(tweet_text) > 4000:
        tweet_text = tweet_text[:3997] + "..."

    # Extract images, links, video from RSS summary
    images = extract_images(summary)
    links = extract_links(summary)
    has_vid = has_video(summary)

    # Build the main embed - cleaner layout
    type_labels = {"tweet": "", "reply": "Replied", "retweet": "Retweeted", "quote": "Quote"}
    type_label = type_labels.get(tweet_type, "")
    footer_text = f"{type_label} \u00B7 {pubdate}" if type_label else pubdate

    # Build author name with badges
    author_name = f"@{username}"
    if has_vid:
        author_name += " \U0001F3AC"
    if tweet_type == "retweet":
        author_name += " \U0001F501"
    if tweet_type == "quote":
        author_name += " \U0001F4AC"

    embed_data = {
        "author": {"name": author_name, "icon_url": f"https://unavatar.io/twitter/{username}", "url": tweet_url},
        "description": tweet_text,
        "color": color if color else 1942002,
        "url": tweet_url,
        "footer": {"text": footer_text, "icon_url": "https://raw.githubusercontent.com/ventsol/tweetvent/master/static/logo.png"},
    }

    # Add external links as a compact field
    if links:
        links_text = "\n".join([f"\U0001F517 {l}" for l in links[:3]])
        embed_data["fields"] = [{"name": "\U0001F517 Links", "value": links_text, "inline": False}]

    # Add first image as embed image
    if images:
        embed_data["image"] = {"url": images[0]}

    embeds = [embed_data]

    # Extra images as additional embeds (gallery)
    # Additional images as gallery (Discord renders multi-embed as grid)
    for img_url in images[1:5]:
        embeds.append({
            "image": {"url": img_url},
            "color": color if color else 1942002,
            "url": tweet_url,
        })

    type_labels = {"tweet": "tweeted", "reply": "replied", "retweet": "retweeted", "quote": "quote tweeted"}
    label = type_labels.get(tweet_type, "tweeted")
    payload = {
        "embeds": embeds,
        "content": f"**@{username}** {label}",
    }

    resp = requests.post(webhook_url, json=payload)
    if resp.status_code not in (200, 204):
        print(f"   Discord webhook failed (HTTP {resp.status_code})")
        return False
    print(f"   Posted @{username}: {tweet_url}")
    return True


# ── State ────────────────────────────────────────────────────────────────────

# State now stores last_tweet_id per account:
# { "last_tweet_id": { "Ventsol_": "123456", "elonmusk": "789012", ... } }


def load_state():
    """Load the last seen tweet IDs from disk."""
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH) as f:
                data = json.load(f)
                last_id = data.get("last_tweet_id", {})
                if isinstance(last_id, str):
                    return {"Ventsol_": last_id, "_posted": set()}
                state = last_id if isinstance(last_id, dict) else {}
                state["_posted"] = set(data.get("posted", []))
                return state
        except (json.JSONDecodeError, OSError):
            pass
    return {"_posted": set()}


def save_state(state):
    """Persist the last seen tweet IDs."""
    posted = list(state.get("_posted", set()))[-100:]
    clean_state = {k: v for k, v in state.items() if k != "_posted"}
    with open(STATE_PATH, "w") as f:
        json.dump({"last_tweet_id": clean_state, "posted": posted}, f)


# ── Per-Account Logic ───────────────────────────────────────────────────────


def check_account(username, instance, webhook_url, state, color=None, include_words=None, exclude_words=None, auth_token=None, ct0=None):
    """Check one account for new tweets and post them. Returns number posted."""
    print(f"  @{username} ...", end=" ")

    # Try direct Twitter fetch first (faster, no Nitter delay)
    tweets = None
    if auth_token and ct0:
        try:
            from twitter_direct import fetch_tweets_direct
            direct_tweets = fetch_tweets_direct(username, auth_token, ct0)
            if direct_tweets:
                tweets = direct_tweets
        except Exception:
            pass

    # Fall back to Nitter RSS
    if tweets is None:
        try:
            feed = fetch_rss_feed(username, instance)
            tweets = get_own_tweets(feed, username)
        except Exception as e:
            print(f"SKIP - {e}")
            return 0

    if not tweets:
        print("no tweets found.")
        return 0

    since_id = state.get(username)
    posted_set = state.get("_posted", set())
    new_tweets = []
    for t in tweets:
        tid = parse_tweet_id_from_link(t.link)
        if tid and (since_id is None or int(tid) > int(since_id)):
            # Skip if already posted (dedup across direct + Nitter)
            if tid in posted_set:
                continue
            # Filter out replies (tweets starting with @)
            summary = t.get("summary", "")
            tweet_text = BeautifulSoup(summary, 'html.parser').get_text(strip=True) if summary else ""
            # Determine tweet type
            tw_type = getattr(t, 'tweet_type', None)
            if not tw_type:
                if tweet_text and tweet_text.startswith('@'):
                    tw_type = "reply"
                elif not tweet_text or tweet_text.startswith('[RT]'):
                    tw_type = "retweet"
                else:
                    tw_type = "tweet"
            # Apply keyword filters
            if tweet_text and (include_words or exclude_words):
                lower_text = tweet_text.lower()
                # Include filter: tweet must contain at least one keyword
                if include_words:
                    has_include = any(kw.lower() in lower_text for kw in include_words)
                    if not has_include:
                        continue
                # Exclude filter: tweet must NOT contain any keyword
                if exclude_words:
                    has_exclude = any(kw.lower() in lower_text for kw in exclude_words)
                    if has_exclude:
                        continue
            new_tweets.append(t)

    if not new_tweets:
        print("no new tweets.")
        return 0

    # Post oldest first so Discord shows them in order
    new_tweets.reverse()
    count = 0
    for i, tweet in enumerate(new_tweets):
        if i > 0:
            time.sleep(2)  # 2s delay so Discord shows them individually
        tw_type = getattr(tweet, 'tweet_type', None) or "tweet"
        if post_to_discord(webhook_url, tweet, username, color=color, tweet_type=tw_type):
            count += 1

    # Update state with the latest tweet ID for this account
    newest_id = str(max(int(parse_tweet_id_from_link(t.link)) for t in new_tweets))
    state[username] = newest_id
    # Track all posted IDs for dedup
    for t in new_tweets:
        tid = parse_tweet_id_from_link(t.link)
        if tid:
            posted_set.add(tid)
    print(f"{count} new tweet(s).")
    return count


# ── Main Logic ──────────────────────────────────────────────────────────────


def run_once(cfg):
    """Check all accounts and post new tweets to Discord."""
    accounts = cfg["twitter"]["accounts"]
    webhook_url = cfg["discord"]["webhook_url"]
    instance = cfg["bot"].get("nitter_instance", "nitter.net")
    colors = cfg.get("colors", {})
    filters_cfg = cfg.get("filters", {})
    auth_token = cfg.get("auth", {}).get("auth_token")
    ct0 = cfg.get("auth", {}).get("ct0")
    state = load_state()

    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] Checking {len(accounts)} account(s)...")

    total = 0
    for username in accounts:
        color = hex_to_int(colors.get(username.strip()))
        # Get keyword filters for this account
        inc = filters_cfg.get("include", {}).get(username.strip(), "")
        exc = filters_cfg.get("exclude", {}).get(username.strip(), "")
        include_words = [w.strip() for w in inc.split(",") if w.strip()] if inc else None
        exclude_words = [w.strip() for w in exc.split(",") if w.strip()] if exc else None
        total += check_account(username.strip(), instance, webhook_url, state,
            color=color, include_words=include_words, exclude_words=exclude_words,
            auth_token=auth_token, ct0=ct0)

    save_state(state)
    print(f"  Total: {total} new tweet(s).")
    return total


def run_loop(cfg):
    """Run the polling loop forever."""
    interval = cfg["bot"].get("poll_interval_minutes", 0.5) * 60
    accounts = cfg["twitter"]["accounts"]
    print(f"Watching: @{', @'.join(accounts)}")
    print(f"Polling every {cfg['bot']['poll_interval_minutes']} minutes. Press Ctrl+C to stop.\n")

    while True:
        try:
            run_once(cfg)
        except Exception as e:
            print(f"Error: {e}")
        print(f"\nWaiting {cfg['bot']['poll_interval_minutes']} min...\n")
        time.sleep(interval)


# ── Entry Point ─────────────────────────────────────────────────────────────


def main():
    print("=" * 50)
    print("  TweetVent v0.2.1")
    print("  (Direct Twitter + Nitter RSS)")
    print("=" * 50)

    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        sys.exit(1)

    if "--loop" in sys.argv:
        run_loop(config)
    else:
        count = run_once(config)
        print(f"\nDone. {count} tweet(s) posted.")
        print("Run with --loop to keep watching.")


if __name__ == "__main__":
    main()
