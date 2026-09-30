"""Parser layer: how X-Reader understands raw responses.

Parsers are pure functions of ``(bytes, unit) -> raw records``. No network, no
storage, no deduplication, no output formatting.

Each source family has its own raw record shape (``RawTweet``, ``RawRSSItem``,
``RawYouTubeItem``); turning those into the shared contract is the Normalizer's
job, not a parser's.
"""
from parsers.base import BaseParser
from parsers.media_url import resolve_media_url
from parsers.nitter_html import NitterHtmlParser
from parsers.nitter_rss import NitterRssParser
from parsers.rss import RssParser
from parsers.rss import parse_feed_time
from parsers.timeutil import parse_source_date

__all__ = [
    "BaseParser",
    "NitterHtmlParser",
    "NitterRssParser",
    "RssParser",
    "parse_feed_time",
    "parse_source_date",
    "resolve_media_url",
]
