"""
TweetVent v0.1.7 — Core bot logic
Runs in a background thread in the web app.
"""

import json
import threading
import time
import tomllib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

CONFIG_PATH = Path(__file__).parent / "config.toml"
STATE_PATH = Path(__file__).parent / "last_tweet.json"
RECENT_PATH = Path(__file__).parent / "recent_tweets.json"


def load_config():
    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return cfg


def save_config(cfg):
    """Write config back to config.toml."""
    import toml

    with open(CONFIG_PATH, "w") as f:
        toml.dump(cfg, f)


def parse_tweet_id_from_link(link):
    path = urlparse(link).path.rstrip("/")
    parts = path.split("/")
    if "status" in parts:
        idx = parts.index("status")
        if idx + 1 < len(parts):
            return parts[idx + 1].split("#")[0]
    return None


def get_own_tweets(feed, username):
    own = []
    for entry in feed.entries:
        author = entry.get("author", "")
        if author.startswith("@"):
            author = author[1:]
        if author.lower() == username.lower():
            own.append(entry)
    return own


class DiscordBot:
    """TweetVent bot that runs in a background thread."""

    def __init__(self, config_path=None, state_path=None):
        self.config_path = config_path or CONFIG_PATH
        self.state_path = state_path or STATE_PATH
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Shared state readable from the web UI
        self.status = "stopped"  # running, stopped, error
        self.last_check_time = None
        self.last_check_result = ""
        self.recent_tweets = deque(maxlen=50)  # (username, tweet_url, text, time)
        self.logs = deque(maxlen=100)
        self.account_stats = {}  # username -> count of tweets posted this session
        self.direct_fetch_ok = True  # True if last direct fetch succeeded, False if fell back to Nitter
        self.account_health = {}  # username -> 'ok', 'rate_limited', 'no_tweets'

        # Load recent tweets from disk to survive restarts
        recent_file = Path(__file__).parent / "recent_tweets.json"
        if recent_file.exists():
            try:
                with open(recent_file) as f:
                    data = json.load(f)
                    for item in data:
                        self.recent_tweets.append(tuple(item))
            except (json.JSONDecodeError, OSError):
                pass

    def _log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        with self._lock:
            self.logs.append(entry)
        print(entry)

    def _load_state(self):
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    data = json.load(f)
                    last_id = data.get("last_tweet_id", {})
                    if isinstance(last_id, str):
                        return {"Ventsol_": last_id}
                    # Return a dict with last_id and a set of posted IDs for dedup
                    state = last_id if isinstance(last_id, dict) else {}
                    state["_posted"] = set(data.get("posted", []))
                    return state
            except (json.JSONDecodeError, OSError):
                pass
        return {"_posted": set()}

    def _save_state(self, state):
        posted = list(state.get("_posted", set()))[-100:]  # keep last 100
        # Remove _posted before saving (it's not a tweet ID map)
        clean_state = {k: v for k, v in state.items() if k != "_posted"}
        with open(self.state_path, "w") as f:
            json.dump({"last_tweet_id": clean_state, "posted": posted}, f)

    def _to_twitter_cdn_url(self, nitter_url):
        """Convert a Nitter /pic/ URL to the original Twitter CDN URL."""
        if '/pic/' not in nitter_url:
            return nitter_url
        from urllib.parse import unquote
        decoded = unquote(nitter_url)
        path = decoded.split('/pic/', 1)[1]
        if path.startswith('pbs.twimg.com'):
            return 'https://' + path
        return f'https://pbs.twimg.com/{path}'

    def _extract_images(self, summary_html):
        """Extract image URLs from the Nitter RSS summary HTML."""
        if not summary_html:
            return []
        soup = BeautifulSoup(summary_html, 'html.parser')
        images = []
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if src and src.startswith('https://'):
                # Convert Nitter proxy URLs to original Twitter CDN URLs
                src = self._to_twitter_cdn_url(src)
                images.append(src)
        return images

    def _extract_links(self, summary_html):
        """Extract external links (not Twitter/Nitter) from summary HTML."""
        if not summary_html:
            return []
        soup = BeautifulSoup(summary_html, 'html.parser')
        links = []
        for a in soup.find_all('a'):
            href = a.get('href', '')
            if href and href.startswith('http') and not any(d in href for d in ['nitter.net', 'twitter.com', 'x.com', '/status/', '/search']):
                links.append(href)
        return links[:3]  # Max 3 links

    def _has_video(self, summary_html):
        """Check if summary contains video content."""
        return 'amplify_video' in summary_html or 'tweet_video_thumb' in summary_html

    @staticmethod
    def _infer_tweet_type(tweet_text):
        """Infer tweet type from text when direct fetch doesn't provide it."""
        if not tweet_text:
            return "retweet"
        if tweet_text.startswith('RT'):
            return "retweet"
        if tweet_text.startswith('@'):
            return "reply"
        return "tweet"

    @staticmethod
    def _hex_to_int(hex_color):
        """Convert a hex color string (e.g. #1DA1F2) to Discord integer."""
        if not hex_color or not isinstance(hex_color, str):
            return 1942002  # default Twitter blue
        hex_color = hex_color.lstrip('#')
        try:
            return int(hex_color, 16)
        except (ValueError, TypeError):
            return 1942002

    def _post_to_discord(self, webhook_url, entry, username, color=None, tweet_type="tweet"):
        tweet_id = parse_tweet_id_from_link(entry.link)
        if not tweet_id:
            return False

        tweet_url = f"https://twitter.com/{username}/status/{tweet_id}"
        pubdate = entry.get("published", "unknown")
        summary = entry.get("summary", "")

        # Use summary (strip HTML) for full tweet text — title is truncated by Nitter
        tweet_text = BeautifulSoup(summary, 'html.parser').get_text(strip=True) if summary else ""
        if not tweet_text:
            tweet_text = entry.get("title", "")

        # Discord embed description limit is 4096 chars
        if len(tweet_text) > 4000:
            tweet_text = tweet_text[:3997] + "..."

        # Extract images and links from the RSS summary HTML
        images = self._extract_images(summary)
        links = self._extract_links(summary)
        has_video = self._has_video(summary)

        # Add media badges to description
        badges = []
        if has_video:
            badges.append("\U0001F3AC Video")  # video camera emoji
        if tweet_type == "retweet":
            badges.append("\U0001F501 Retweet")  # repeat emoji
        if tweet_type == "quote":
            badges.append("\U0001F4AC Quote")  # speech bubble emoji
        if badges:
            tweet_text = " ".join(badges) + "\n\n" + tweet_text

        # Build embed
        profile_pic = f"https://unavatar.io/twitter/{username}"
        embed_data = {
            "author": {"name": f"@{username}", "icon_url": profile_pic},
            "title": "View on X",
            "description": tweet_text,
            "color": color if color else 1942002,
            "url": tweet_url,
            "footer": {"text": pubdate},
        }

        # Add external links as a field
        if links:
            links_text = "\n".join(links[:3])
            embed_data["fields"] = [{"name": "\U0001F517 Links", "value": links_text, "inline": False}]

        # Add first image as the embed thumbnail
        if images:
            embed_data["image"] = {"url": images[0]}

        embeds = [embed_data]

        # If there are more images, add extra embeds (up to 4 more for gallery feel)
        # Discord allows up to 10 embeds per webhook
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

        resp = requests.post(webhook_url, json=payload, timeout=15)
        if resp.status_code not in (200, 204):
            self._log(f"Discord webhook failed (HTTP {resp.status_code})")
            return False

        with self._lock:
            self.recent_tweets.appendleft((username, tweet_url, tweet_text[:100], str(datetime.now())))
            # Track per-account stats
            self.account_stats[username] = self.account_stats.get(username, 0) + 1
            # Save to disk so it persists across restarts
            try:
                with open(RECENT_PATH, "w") as f:
                    json.dump(list(self.recent_tweets), f)
            except OSError:
                pass
        return True

    def _check_account(self, username, instance, webhook_url, state, color=None, include_words=None, exclude_words=None, auth_token=None, ct0=None):
        # Try direct Twitter fetch first (faster, no Nitter delay)
        tweets = None
        if auth_token and ct0:
            try:
                from twitter_direct import fetch_tweets_direct
                direct_tweets = fetch_tweets_direct(username, auth_token, ct0)
                if direct_tweets:
                    tweets = direct_tweets
                    self.direct_fetch_ok = True
                else:
                    self.direct_fetch_ok = False
                    self._log(f"@{username}: Direct fetch timed out - cookies may need refresh, falling back to Nitter")
            except Exception as e:
                self.direct_fetch_ok = False
                self._log(f"@{username}: Direct fetch failed ({e}), falling back to Nitter")
        
        # Fall back to Nitter RSS if direct fetch didn't work
        if tweets is None:
            url = f"https://{instance}/{username}/rss"
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
        self._log(f"@{username}: RSS feed failed — instance may be down")
            self.account_health[username] = "rate_limited"
            return 0
            tweets = get_own_tweets(feed, username)
        
        if not tweets:
            self.account_health[username] = "no_tweets"
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
                # Extract tweet text for filtering
                summary = t.get("summary", "")
                tweet_text = BeautifulSoup(summary, 'html.parser').get_text(strip=True) if summary else ""
                # Get tweet type (from direct fetch) or infer it
                tweet_type = getattr(t, 'tweet_type', None) or _infer_tweet_type(tweet_text)
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

        self.account_health[username] = "ok"
        if not new_tweets:
            return 0

        new_tweets.reverse()  # oldest first
        count = 0
        for i, tweet in enumerate(new_tweets):
            if i > 0:
                time.sleep(2)  # 2s delay between tweets so Discord shows them individually
            tw_type = getattr(tweet, 'tweet_type', None) or self._infer_tweet_type(tweet_text)
            if self._post_to_discord(webhook_url, tweet, username, color=color, tweet_type=tw_type):
                count += 1

        newest_id = str(max(int(parse_tweet_id_from_link(t.link)) for t in new_tweets))
        state[username] = newest_id
        # Track all posted IDs for dedup
        for t in new_tweets:
            tid = parse_tweet_id_from_link(t.link)
            if tid:
                posted_set.add(tid)
        return count

    def run_once(self):
        """Run one check cycle on all accounts."""
        try:
            cfg = load_config()
        except Exception as e:
            self._log(f"Config error: {e}")
            return

        accounts = cfg["twitter"].get("accounts", [])
        webhook_url = cfg["discord"].get("webhook_url", "")
        instance = cfg["bot"].get("nitter_instance", "nitter.net")
        colors = cfg.get("colors", {})
        auth_token = cfg.get("auth", {}).get("auth_token")
        ct0 = cfg.get("auth", {}).get("ct0")
        state = self._load_state()

        total = 0
        for username in accounts:
            if self._stop_event.is_set():
                break
            try:
                color = self._hex_to_int(colors.get(username.strip()))
                # Get keyword filters for this account
                filters_cfg = cfg.get("filters", {})
                inc = filters_cfg.get("include", {}).get(username.strip(), "")
                exc = filters_cfg.get("exclude", {}).get(username.strip(), "")
                include_words = [w.strip() for w in inc.split(",") if w.strip()] if inc else None
                exclude_words = [w.strip() for w in exc.split(",") if w.strip()] if exc else None
                total += self._check_account(username.strip(), instance, webhook_url, state,
                    color=color, include_words=include_words, exclude_words=exclude_words,
                    auth_token=auth_token, ct0=ct0)
            except Exception as e:
                self._log(f"@{username}: Error — {e}")

        self._save_state(state)
        self.last_check_time = datetime.now().strftime("%H:%M:%S")
        self.last_check_result = f"{total} new tweet(s) from {len(accounts)} account(s)"
        self._log(f"Checked {len(accounts)} account(s) — {total} new")

    def _loop(self):
        self._log("Bot started")
        self.last_check_time = datetime.now().strftime("%H:%M:%S")

        while not self._stop_event.is_set():
            self.run_once()
            try:
                cfg = load_config()
                interval = cfg["bot"].get("poll_interval_minutes", 0.5) * 60
            except Exception:
                interval = 30  # default 30 seconds

            # Wait for the interval, but check stop event every second
            for _ in range(int(interval)):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

        self.status = "stopped"
        self._log("Bot stopped")

    def start(self):
        if self._thread and self._thread.is_alive():
            self._log("Bot is already running")
            return
        self._stop_event.clear()
        self.status = "running"
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._log("Bot starting...")

    def stop(self):
        self._stop_event.set()
        self.status = "stopping"
        self._log("Bot stopping...")

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def get_logs(self, n=30):
        with self._lock:
            return list(self.logs)[-n:]

    def get_recent_tweets(self, n=20):
        with self._lock:
            return list(self.recent_tweets)[:n]
