"""
Fast Twitter fetcher using Playwright headless browser.
Browser is opened once and reused across checks for speed.
"""

import threading
from datetime import datetime, timezone


class TweetEntry:
    """Mimics a feedparser entry so the existing bot code works unchanged."""
    def __init__(self, title, link, summary, published, author):
        self.title = title
        self.link = link
        self.summary = summary
        self.published = published
        self.author = author

    def get(self, key, default=None):
        return getattr(self, key, default)


class TwitterFetcher:
    """Reusable Twitter fetcher that keeps a browser alive between checks."""

    def __init__(self, auth_token, ct0):
        self.auth_token = auth_token
        self.ct0 = ct0
        self._lock = threading.Lock()
        self._playwright = None
        self._browser = None
        self._context = None

    def _ensure_browser(self):
        """Start browser if not already running."""
        if self._browser and self._context:
            try:
                # Quick check if browser is still alive
                self._context.pages
                return
            except:
                pass

        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1280, "height": 720},
        )
        self._context.add_cookies([
            {"name": "auth_token", "value": self.auth_token, "domain": ".x.com", "path": "/"},
            {"name": "ct0", "value": self.ct0, "domain": ".x.com", "path": "/"},
        ])

    def close(self):
        """Clean up the browser."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except:
            pass
        self._context = None
        self._browser = None
        self._playwright = None

    def fetch(self, username):
        """Fetch tweets for a username. Reuses the persistent browser."""
        with self._lock:
            try:
                self._ensure_browser()
                page = self._context.new_page()
                page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=30000)

                # Wait for tweets to load (up to 8s, but often much faster)
                try:
                    page.wait_for_selector('article[data-testid="tweet"]', timeout=8000)
                except:
                    pass
                page.wait_for_timeout(2000)

                # Extract tweets
                data = page.evaluate("""(username) => {
                    const articles = document.querySelectorAll('article[data-testid="tweet"]');
                    const results = [];
                    for (const article of articles) {
                        const textEl = article.querySelector('[data-testid="tweetText"]');
                        const linkEl = article.querySelector('a[href*="/status/"]');
                        const timeEl = article.querySelector('time');
                        const text = textEl ? textEl.textContent : '';
                        const link = linkEl ? linkEl.href : '';
                        const time = timeEl ? timeEl.getAttribute('datetime') : '';

                        if (!link) continue;

                        // Extract images
                        const imgSrcs = [];
                        const imgs = article.querySelectorAll('img[src*="pbs.twimg.com/media"]');
                        for (const img of imgs) {
                            if (img.src && !img.src.includes('profile_images')) {
                                imgSrcs.push(img.src);
                            }
                        }
                        // Video posters
                        const videos = article.querySelectorAll('video[poster]');
                        for (const v of videos) {
                            if (v.poster && v.poster.includes('pbs.twimg.com')) {
                                imgSrcs.push(v.poster);
                            }
                        }

                        // Check for quoted tweet
                        let displayText = text;
                        const quoted = article.querySelector('[data-testid="card.wrapper"]');
                        if (quoted) {
                            const qt = quoted.querySelector('[data-testid="tweetText"]');
                            if (qt && qt.textContent) {
                                const prefix = text ? '\\n\\n[Quote] ' : '[Quote] ';
                                displayText = text + prefix + qt.textContent;
                            }
                        }

                        results.push({
                            text: displayText,
                            link: link,
                            time: time,
                            author: username,
                            images: imgSrcs,
                        });
                    }
                    return results;
                }""", username)

                page.close()

                if not data:
                    return None

                entries = []
                for t in data:
                    text = t.get("text", "")
                    published = t.get("time", "")
                    link = t.get("link", "")
                    images = t.get("images", [])

                    summary = text
                    if images:
                        img_html = "".join([f'<img src="{img}">' for img in images[:6]])
                        summary = text + img_html

                    entries.append(TweetEntry(
                        title=text[:200] if text else "",
                        link=link,
                        summary=summary,
                        published=published,
                        author=username,
                    ))

                return entries

            except Exception as e:
                # If browser died, reset it for next time
                self.close()
                return None


# Module-level singleton for reuse
_fetcher_instance = None
_fetcher_lock = threading.Lock()


def fetch_tweets_direct(username, auth_token, ct0):
    """
    Fetch tweets using the shared persistent browser.
    Much faster than creating a new browser each time.
    """
    global _fetcher_instance

    with _fetcher_lock:
        if _fetcher_instance is None:
            _fetcher_instance = TwitterFetcher(auth_token, ct0)
        elif _fetcher_instance.auth_token != auth_token or _fetcher_instance.ct0 != ct0:
            _fetcher_instance.close()
            _fetcher_instance = TwitterFetcher(auth_token, ct0)

    return _fetcher_instance.fetch(username)
