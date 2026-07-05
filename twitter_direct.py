"""
Twitter fetcher - uses auth tokens (Playwright) first, then Nitter RSS fallback.
"""

import feedparser


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
    Fetch tweets for a user.
    Priority: 1) Playwright with auth cookies 2) Nitter RSS
    """
    # Try Playwright first (instant, uses auth cookies)
    tweets = _try_playwright(username, auth_token, ct0)
    if tweets:
        return tweets

    # Fall back to Nitter RSS
    return _nitter_fetch(username)


def _try_playwright(username, auth_token, ct0):
    """Try fetching tweets directly via Playwright with auth cookies."""
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
            page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=15000)

            try:
                page.wait_for_selector('article[data-testid="tweet"]', timeout=6000)
            except:
                pass
            page.wait_for_timeout(2000)

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
                    if (!link || !link.includes('/' + username + '/status/')) continue;

                    // Images
                    const imgSrcs = [];
                    const imgs = article.querySelectorAll('img[src*="pbs.twimg.com/media"]');
                    for (const img of imgs) {
                        if (img.src && !img.src.includes('profile_images')) imgSrcs.push(img.src);
                    }
                    const videos = article.querySelectorAll('video[poster]');
                    for (const v of videos) {
                        if (v.poster && v.poster.includes('pbs.twimg.com')) imgSrcs.push(v.poster);
                    }

                    // Quoted tweet
                    let displayText = text;
                    const quoted = article.querySelector('[data-testid="card.wrapper"]');
                    if (quoted) {
                        const qt = quoted.querySelector('[data-testid="tweetText"]');
                        if (qt && qt.textContent) {
                            const prefix = text ? '\\\\n\\\\n[Quote] ' : '[Quote] ';
                            displayText = text + prefix + qt.textContent;
                        }
                    }

                    // Tweet type detection
                    let tweetType = 'tweet';
                    const retweetedSpan = article.querySelector('span[data-testid="socialContext"]');
                    if (retweetedSpan && retweetedSpan.textContent.includes('Retweeted')) {
                        tweetType = 'retweet';
                    } else if (quoted) {
                        tweetType = 'quote';
                    } else if (text && text.trimStart().startsWith('@')) {
                        tweetType = 'reply';
                    }

                    results.push({ text: displayText, link, time, author: username, images: imgSrcs, type: tweetType });
                }
                return results;
            }""", username)

            browser.close()

            if not data:
                return None

            entries = []
            for t in data:
                images = t.get("images", [])
                summary = t.get("text", "")
                if images:
                    img_html = "".join([f'<img src="{img}">' for img in images[:6]])
                    summary = summary + img_html

                entry = TweetEntry(
                    title=t.get("text", "")[:200],
                    link=t.get("link", ""),
                    summary=summary,
                    published=t.get("time", ""),
                    author=username,
                )
                entry.tweet_type = t.get("type", "tweet")
                entries.append(entry)

            return entries

    except Exception:
        return None


def _nitter_fetch(username):
    """Fallback: fetch from Nitter RSS."""
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
                    entry = TweetEntry(
                        title=e.get("title", ""),
                        link=e.get("link", ""),
                        summary=e.get("summary", ""),
                        published=e.get("published", ""),
                        author=e.get("author", ""),
                    )
                    title_text = e.get("title", "")
                    if title_text.startswith("RT"):
                        entry.tweet_type = "retweet"
                    else:
                        entry.tweet_type = "tweet"
                    entries.append(entry)
                return entries
        except:
            continue
    return None
