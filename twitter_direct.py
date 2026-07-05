"""
Twitter fetcher using Playwright headless browser.
Direct fetch from Twitter - no Nitter delay.
"""

from bs4 import BeautifulSoup


class TweetEntry:
    def __init__(self, title, link, summary, published, author):
        self.title = title
        self.link = link
        self.summary = summary
        self.published = published
        self.author = author

    def get(self, key, default=None):
        return getattr(self, key, default)


def fetch_tweets_direct(username, auth_token, ct0):
    """
    Fetch tweets directly from Twitter using Playwright.
    Returns list of TweetEntry objects or None on failure.
    Falls back to Nitter RSS if Playwright fails.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 720},
            )
            context.add_cookies([
                {"name": "auth_token", "value": auth_token, "domain": ".x.com", "path": "/"},
                {"name": "ct0", "value": ct0, "domain": ".x.com", "path": "/"},
            ])

            page = context.new_page()
            page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=45000)

            # Wait for tweets to render
            try:
                page.wait_for_selector('article[data-testid="tweet"]', timeout=10000)
            except:
                pass
            page.wait_for_timeout(3000)

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

                    // Only include tweets by the target user
                    if (!link.includes('/' + username + '/status/')) continue;

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

            browser.close()

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

    except Exception:
        # Fall back to Nitter RSS
        return _nitter_fallback(username)


def _nitter_fallback(username):
    """Fallback: fetch from Nitter RSS if Playwright fails."""
    import feedparser

    instances = ["nitter.net", "xcancel.com", "nitter.tiekoetter.com"]
    for instance in instances:
        try:
            url = f"https://{instance}/{username}/rss"
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                continue
            if feed.entries:
                entries = []
                for e in feed.entries:
                    entries.append(TweetEntry(
                        title=e.get("title", ""),
                        link=e.get("link", ""),
                        summary=e.get("summary", ""),
                        published=e.get("published", ""),
                        author=e.get("author", ""),
                    ))
                return entries
        except:
            continue
    return None
