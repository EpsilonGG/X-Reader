"""RawTweet — the Phase 1 record produced by a Parser and persisted by Storage.

Design notes (architecture freeze, sections 9 / 12 / 13):

* **Raw-data first.** Every field below is a *projection* of what the source
  actually rendered. The untouched parser output is preserved verbatim in
  ``raw`` so a future Normalizer can re-derive a better model without
  re-fetching anything.
* **No premature universal model.** Only fields that Nitter's RSS and HTML
  actually expose are modelled. Nothing is added "because it might be useful".
* **Timestamps stay strings.** ``created_at`` is ISO-8601 when the source
  rendered an absolute date, otherwise ``None``. ``created_at_raw`` always
  keeps the source's own rendering. Strings are used deliberately: no
  coercion, no timezone guessing, no information loss.
* ``tweet_id`` is the primary identity (freeze, section 10).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.media import Media


@dataclass(slots=True)
class Stats:
    """Engagement counters as rendered by the source.

    ``None`` means "the source did not render a number" (Nitter renders an
    empty views span for tweets without view data) — which is different from
    a real zero.
    """

    replies: int | None = None
    retweets: int | None = None
    likes: int | None = None
    views: int | None = None

    def is_empty(self) -> bool:
        return all(v is None for v in (self.replies, self.retweets, self.likes, self.views))

    def to_dict(self) -> dict:
        return {
            "replies": self.replies,
            "retweets": self.retweets,
            "likes": self.likes,
            "views": self.views,
        }


@dataclass(slots=True)
class RawTweet:
    """One tweet as scraped, before any normalization."""

    # --- identity -----------------------------------------------------------
    tweet_id: str
    account: str
    url: str

    # --- content ------------------------------------------------------------
    text: str = ""
    created_at: str | None = None
    created_at_raw: str = ""

    # --- author -------------------------------------------------------------
    author_username: str = ""
    author_name: str = ""

    # --- relationships ------------------------------------------------------
    is_retweet: bool = False
    retweeted_by: str | None = None
    is_reply: bool = False
    reply_to: str | None = None
    is_quote: bool = False
    quoted_tweet_id: str | None = None
    quoted_author: str | None = None
    quoted_text: str | None = None

    # --- extras -------------------------------------------------------------
    is_pinned: bool = False
    media: list[Media] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)

    # --- provenance ---------------------------------------------------------
    provider: str = ""
    route: str = ""
    fetched_at: str = ""

    # --- evidence -----------------------------------------------------------
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialise to a JSON-native mapping (used as one JSONL line)."""
        return {
            "tweet_id": self.tweet_id,
            "account": self.account,
            "url": self.url,
            "text": self.text,
            "created_at": self.created_at,
            "created_at_raw": self.created_at_raw,
            "author_username": self.author_username,
            "author_name": self.author_name,
            "is_retweet": self.is_retweet,
            "retweeted_by": self.retweeted_by,
            "is_reply": self.is_reply,
            "reply_to": self.reply_to,
            "is_quote": self.is_quote,
            "quoted_tweet_id": self.quoted_tweet_id,
            "quoted_author": self.quoted_author,
            "quoted_text": self.quoted_text,
            "is_pinned": self.is_pinned,
            "media": [m.to_dict() for m in self.media],
            "stats": self.stats.to_dict(),
            "provider": self.provider,
            "route": self.route,
            "fetched_at": self.fetched_at,
            "raw": self.raw,
        }
