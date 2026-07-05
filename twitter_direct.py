"""
Direct Twitter fetcher using Playwright (headless browser).
No Nitter delay, no API key needed - uses saved auth cookies.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urlparse


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


def fetch_tweets_direct(username, auth_token, ct0, max_retries=2):
    """
    Fetch tweets directly from Twitter using Playwright with saved cookies.
    Returns a list of TweetEntry objects (same format as feedparser entries).
    Returns None if it completely fails (caller should fall back to Nitter).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    for attempt in range(max_retries):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    viewport={"width": 1280, "height": 720},
                )
                
                # Set auth cookies
                context.add_cookies([
                    {"name": "auth_token", "value": auth_token, "domain": ".x.com", "path": "/"},
                    {"name": "ct0", "value": ct0, "domain": ".x.com", "path": "/"},
                ])
                
                page = context.new_page()
                page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=45000)
                # Wait for tweets to actually render (up to 10s)
                try:
                    page.wait_for_selector('article[data-testid="tweet"]', timeout=10000)
                except:
                    pass
                page.wait_for_timeout(3000)
                
                # Extract tweets from the rendered page
                tweets_data = page.evaluate("""(username) => {
                    const articles = document.querySelectorAll('article[data-testid="tweet"]');
                    const results = [];
                    for (const article of articles) {
                        const textEl = article.querySelector('[data-testid="tweetText"]');
                        const linkEl = article.querySelector('a[href*="/status/"]');
                        const timeEl = article.querySelector('time');
                        let text = textEl ? textEl.textContent : '';
                        const link = linkEl ? linkEl.href : '';
                        const time = timeEl ? timeEl.getAttribute('datetime') : '';
                        
                        // Extract image URLs from the tweet (initialize first, used by quote check too)
                        const imageUrls = [];
                        const imgEls = article.querySelectorAll('img[alt="Image"], img[alt="Img"], div[data-testid="tweetPhoto"] img, img[src*="pbs.twimg.com/media"]');
                        for (const img of imgEls) {
                            const src = img.getAttribute('src');
                            if (src && src.includes('pbs.twimg.com') && !src.includes('profile_images')) {
                                imageUrls.push(src);
                            }
                        }
                        
                        // Extract quoted tweet content (quote tweets)
                        const quotedCard = article.querySelector('[data-testid="card.wrapper"]');
                        let quotedText = '';
                        if (quotedCard) {
                            const quotedTextEl = quotedCard.querySelector('[data-testid="tweetText"]');
                            if (quotedTextEl) {
                                quotedText = quotedTextEl.textContent || '';
                                if (text) {
                                    text = text + '\\n\\n[Quote] ' + quotedText;
                                } else {
                                    text = '[Quote] ' + quotedText;
                                }
                            }
                            // Also get quoted tweet images
                            const quotedImgs = quotedCard.querySelectorAll('img[src*="pbs.twimg.com/media"]');
                            for (const img of quotedImgs) {
                                const src = img.getAttribute('src');
                                if (src && !src.includes('profile_images')) {
                                    if (!imageUrls.includes(src)) imageUrls.push(src);
                                }
                            }
                        }
                        
                        // Also check for video thumbnails
                        const videoEls = article.querySelectorAll('video[poster]');
                        for (const video of videoEls) {
                            const poster = video.getAttribute('poster');
                            if (poster && poster.includes('pbs.twimg.com')) {
                                imageUrls.push(poster);
                            }
                        }
                        
                        if (link) {
                            results.push({ text, link, time, author: username, images: imageUrls });
                        }
                    }
                    return results;
                }""", username)
                
                browser.close()
                
                if not tweets_data:
                    return None
                
                # Convert to TweetEntry format (same as feedparser)
                entries = []
                for t in tweets_data:
                    text = t.get("text", "")
                    published = t.get("time", "")
                    link = t.get("link", "")
                    images = t.get("images", [])
                    
                    # Build summary with images embedded as HTML (same format as Nitter RSS)
                    # so the existing _extract_images method can parse them
                    summary = text
                    if images:
                        img_html = "".join([f'<img src="{img}">' for img in images])
                        summary = f"{text}{img_html}"
                    
                    entries.append(TweetEntry(
                        title=text[:200] if text else "",
                        link=link,
                        summary=summary,
                        published=published,
                        author=username,
                    ))
                
                return entries
                
        except Exception as e:
            if attempt < max_retries - 1:
                continue
            return None

    return None
