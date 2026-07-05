"""
Nitter RSS fetcher - simple and reliable.
Falls back to different instances if one is down.
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
    Fetches tweets via Nitter RSS.
    Auth params kept for compatibility - not used directly.
    Falls back through multiple Nitter instances if needed.
    """
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
