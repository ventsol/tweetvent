"""
Twitter fetcher - uses Nitter RSS by default, Playwright as optional enhancement.
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
    Primary: Nitter RSS (reliable)
    """
    # Nitter RSS (primary - always works)
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
                    # Detect tweet type from Nitter data
                    author = e.get("author", "")
                    title_text = e.get("title", "")
                    if title_text.startswith("RT"):
                        entry.tweet_type = "retweet"
                    elif author and author.startswith("@"):
                        entry.tweet_type = "reply"
                    else:
                        entry.tweet_type = "tweet"
                    entries.append(entry)
                return entries
        except:
            continue

    return None
