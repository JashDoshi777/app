"""
Sentiment Analysis Engine.
Aggregates sentiment from RSS feeds, Reddit, and Twitter/X.
Uses VADER for fast scoring.
"""

import logging
import re
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)

# ── Lazy imports ──────────────────────────────────────────
try:
    import feedparser
    FEEDPARSER_OK = True
except ImportError:
    FEEDPARSER_OK = False

try:
    import praw
    PRAW_OK = True
except ImportError:
    PRAW_OK = False

try:
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    import nltk
    try:
        nltk.data.find("sentiment/vader_lexicon.zip")
    except LookupError:
        nltk.download("vader_lexicon", quiet=True)
    VADER_OK = True
except ImportError:
    VADER_OK = False


class SentimentEngine:
    """Multi-source sentiment analysis for Indian markets."""

    def __init__(self):
        self.vader = SentimentIntensityAnalyzer() if VADER_OK else None
        self.reddit = None
        if PRAW_OK and config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET:
            try:
                self.reddit = praw.Reddit(
                    client_id=config.REDDIT_CLIENT_ID,
                    client_secret=config.REDDIT_CLIENT_SECRET,
                    user_agent=config.REDDIT_USER_AGENT,
                )
            except Exception as e:
                logger.error("Reddit init failed: %s", e)

        self.history: list[dict] = []

        # Financial word adjustments for VADER
        self._fin_lexicon = {
            "bullish": 2.5, "bearish": -2.5, "rally": 2.0, "crash": -3.0,
            "breakout": 2.0, "breakdown": -2.0, "profit": 1.5, "loss": -1.5,
            "buy": 1.0, "sell": -1.0, "surge": 2.0, "plunge": -2.5,
            "uptick": 1.0, "downtick": -1.0, "oversold": 1.5, "overbought": -1.0,
            "accumulation": 1.5, "distribution": -1.5, "support": 1.0,
            "resistance": -0.5, "squeeze": 1.5, "dump": -2.0, "moon": 2.0,
            "rbi": 0.0, "fed": 0.0, "rate cut": 1.5, "rate hike": -1.5,
        }
        if self.vader:
            self.vader.lexicon.update(self._fin_lexicon)

    def analyze_all(self, symbol: str = "NIFTY") -> dict:
        """Run sentiment analysis across all sources."""
        rss_scores = self._analyze_rss(symbol)
        reddit_scores = self._analyze_reddit(symbol)

        all_scores = rss_scores + reddit_scores
        if not all_scores:
            return {
                "aggregate_score": 0, "label": "NEUTRAL",
                "confidence": 0, "source_count": 0,
                "rss": [], "reddit": [], "details": [],
            }

        compounds = [s["compound"] for s in all_scores]
        avg = sum(compounds) / len(compounds)
        confidence = min(100, len(compounds) * 10)

        if avg >= 0.15:
            label = "BULLISH"
        elif avg <= -0.15:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        result = {
            "aggregate_score": round(avg, 4),
            "label": label,
            "confidence": confidence,
            "source_count": len(all_scores),
            "rss": rss_scores[:10],
            "reddit": reddit_scores[:10],
            "timestamp": datetime.now().isoformat(),
        }

        self.history.append(result)
        if len(self.history) > 500:
            self.history = self.history[-500:]

        return result

    def _score_text(self, text: str) -> dict:
        """Score a text string with VADER."""
        if not self.vader:
            return {"compound": 0, "pos": 0, "neg": 0, "neu": 1}
        scores = self.vader.polarity_scores(text)
        return {
            "compound": scores["compound"],
            "pos": scores["pos"],
            "neg": scores["neg"],
            "neu": scores["neu"],
        }

    def _analyze_rss(self, symbol: str = "") -> list[dict]:
        """Fetch and score RSS feed headlines."""
        if not FEEDPARSER_OK:
            return []

        results = []
        for url in config.RSS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:15]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    text = f"{title}. {summary}"

                    # Check relevance
                    if symbol and not self._is_relevant(text, symbol):
                        continue

                    scores = self._score_text(text)
                    results.append({
                        "source": "RSS",
                        "url": url,
                        "title": title,
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        **scores,
                    })
            except Exception as e:
                logger.warning("RSS parse error for %s: %s", url, e)

        return results

    def _analyze_reddit(self, symbol: str = "") -> list[dict]:
        """Fetch and score Reddit posts."""
        if not self.reddit:
            return []

        results = []
        for sub_name in config.REDDIT_SUBREDDITS:
            try:
                subreddit = self.reddit.subreddit(sub_name)
                for post in subreddit.hot(limit=15):
                    text = f"{post.title}. {post.selftext[:500]}"

                    if symbol and not self._is_relevant(text, symbol):
                        continue

                    scores = self._score_text(text)
                    results.append({
                        "source": "REDDIT",
                        "subreddit": sub_name,
                        "title": post.title,
                        "link": f"https://reddit.com{post.permalink}",
                        "score": post.score,
                        "comments": post.num_comments,
                        **scores,
                    })
            except Exception as e:
                logger.warning("Reddit error for r/%s: %s", sub_name, e)

        return results

    def _is_relevant(self, text: str, symbol: str) -> bool:
        """Check if text mentions the symbol or related terms."""
        text_lower = text.lower()
        terms = [symbol.lower(), "nifty", "banknifty", "market", "options",
                 "trading", "sensex", "nse", "bse", "index"]
        return any(t in text_lower for t in terms)

    def get_momentum(self, lookback: int = 10) -> dict:
        """Track sentiment momentum (is sentiment improving or deteriorating?)."""
        if len(self.history) < 2:
            return {"momentum": "STABLE", "change": 0}

        recent = self.history[-lookback:]
        scores = [h["aggregate_score"] for h in recent]

        if len(scores) < 2:
            return {"momentum": "STABLE", "change": 0}

        first_half = sum(scores[:len(scores) // 2]) / max(1, len(scores) // 2)
        second_half = sum(scores[len(scores) // 2:]) / max(1, len(scores) - len(scores) // 2)
        change = second_half - first_half

        return {
            "momentum": "IMPROVING" if change > 0.1 else "DETERIORATING" if change < -0.1 else "STABLE",
            "change": round(change, 4),
            "current": round(scores[-1], 4) if scores else 0,
        }
