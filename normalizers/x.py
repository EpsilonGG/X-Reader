"""X -> NormalizedItem.

Mapping frozen in ``docs/ARCHITECTURE_FREEZE_V3.md`` section 13 and implemented
here for the first time. Two rules are worth stating explicitly because they are
easy to get wrong:

1. **No Telegram markup.** ``[from @username](url)`` is Telegram's rendering, not
   data. This module stores the bare handle in ``publisher`` and the permalink in
   ``canonical_url``; assembling the two into a link is a renderer's job. Adding
   a ``markdown``/``telegram_text`` field here would let one output adapter's
   formatting requirements dictate the shared model.
2. **Only X-specific information goes to ``metadata``.** Engagement counters,
   the retweet/reply/quote relationships and the tracked timeline are real, but
   no other source has them, so they must not become shared fields (freeze
   section 10).

Everything else follows from the raw record: a tweet has no title, its text is
the content, and ``created_at`` may legitimately be absent (Nitter renders a
fresh tweet as a relative time such as ``3h``, which must never be promoted to a
timestamp).
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone

from domain.models.item import PRECISION_SECOND
from domain.models.item import PRECISION_UNKNOWN
from domain.models.item import SOURCE_X
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from domain.models.tweet import RawTweet
from normalizers.base import BaseNormalizer


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class XNormalizer(BaseNormalizer):
    """``RawTweet`` -> ``NormalizedItem``."""

    name = "x"
    #: The source namespace. Never the provider name: the same tweet can arrive
    #: through the ``nitter`` or the ``xtf`` provider and must keep one identity.
    source_id = SOURCE_X

    def normalize_one(self, raw: RawTweet) -> NormalizedItem | None:
        if not isinstance(raw, RawTweet):
            return None

        publisher = self._publisher(raw)
        return NormalizedItem(
            source_id=self.source_id,
            item_id=raw.tweet_id,
            # A tweet has no title. This is why ``title`` is optional.
            title=None,
            content=raw.text,
            publisher=publisher,
            canonical_url=self._url(raw, publisher),
            published_at=raw.created_at,
            published_at_raw=raw.created_at_raw,
            published_precision=(
                PRECISION_SECOND if raw.created_at else PRECISION_UNKNOWN
            ),
            # The runner stamps this from the response; the fallback only fires
            # when a caller drives the normalizer directly.
            fetched_at=raw.fetched_at or _now_iso(),
            media=[
                MediaItem(url=m.url, type=m.type, thumbnail=m.thumbnail)
                for m in raw.media
            ],
            metadata=self._metadata(raw),
        )

    # -- pieces -------------------------------------------------------------
    @staticmethod
    def _publisher(raw: RawTweet) -> str | None:
        """The tweet's author handle, without the ``@``.

        Falls back to the tracked timeline when the markup did not expose an
        author. For a retweet whose author is missing this yields the retweeter
        rather than the original author — which is why ``is_retweet`` and
        ``retweeted_by`` are kept in ``metadata``, so the substitution is
        visible rather than silent.
        """
        return raw.author_username or raw.account or None

    @staticmethod
    def _url(raw: RawTweet, publisher: str | None) -> str | None:
        if raw.url:
            return raw.url
        if publisher and raw.tweet_id:
            return f"https://x.com/{publisher}/status/{raw.tweet_id}"
        return None

    @staticmethod
    def _metadata(raw: RawTweet) -> dict:
        return {
            # The tracked timeline, which is not the author for a retweet.
            "account": raw.account,
            "author_name": raw.author_name,
            "is_retweet": raw.is_retweet,
            "retweeted_by": raw.retweeted_by,
            "is_reply": raw.is_reply,
            "reply_to": raw.reply_to,
            "is_quote": raw.is_quote,
            "quoted_tweet_id": raw.quoted_tweet_id,
            "quoted_author": raw.quoted_author,
            "quoted_text": raw.quoted_text,
            "is_pinned": raw.is_pinned,
            "stats": raw.stats.to_dict(),
            # Provenance of the bytes, not of the content. Kept out of the
            # contract on purpose: the same tweet may be fetched by a different
            # provider next run, and nothing downstream should branch on it.
            "provider": raw.provider,
            "route": raw.route,
        }
