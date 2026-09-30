"""Platform-independent domain models.

Structure inherited from X-rss (`domain/models/`), extended with the raw
records X-Reader's parsers produce and the unified contract its Storage persists.

Three tiers live here, and keeping them distinct is the point:

* **Raw** records, one per source shape — ``RawTweet`` (X), ``RawRSSItem``
  (RSS/Atom), ``RawYouTubeItem`` (interface only). Source-specific, never
  unified, each carrying a verbatim ``raw`` payload so a better model can be
  derived later without refetching.
* **NormalizedItem**, the single cross-source contract. Produced by a Normalizer,
  persisted by Storage, consumed by Output Adapters.
* **Supporting value objects** — ``Account`` (a tracked X handle), ``Media``
  (X attachments), ``Stats`` (X engagement counters).

Deliberate deviation from X-rss: these are dataclasses, not Pydantic models.
Raw-data fidelity is the reason — dataclasses perform no coercion, so what the
parser extracted is exactly what gets written to storage, and
``dataclasses``-based ``to_dict()`` yields JSON-native values with no custom
encoder. Pydantic is still used for *configuration* (X-rss's convention),
where validation and coercion are exactly what you want.
"""
from domain.models.account import Account
from domain.models.item import GIF
from domain.models.item import IMAGE
from domain.models.item import MEDIA_TYPES
from domain.models.item import PRECISION_DAY
from domain.models.item import PRECISION_HOUR
from domain.models.item import PRECISION_MINUTE
from domain.models.item import PRECISION_SECOND
from domain.models.item import PRECISION_UNKNOWN
from domain.models.item import PRECISIONS
from domain.models.item import RESERVED_SOURCE_IDS
from domain.models.item import SOURCE_X
from domain.models.item import SOURCE_YOUTUBE
from domain.models.item import VIDEO
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from domain.models.item import build_identity_key
from domain.models.media import Media
from domain.models.rss_item import RawRSSItem
from domain.models.tweet import RawTweet
from domain.models.tweet import Stats
from domain.models.youtube_item import RawYouTubeItem

__all__ = [
    # raw records
    "RawTweet",
    "RawRSSItem",
    "RawYouTubeItem",
    # unified contract
    "NormalizedItem",
    "MediaItem",
    "build_identity_key",
    "SOURCE_X",
    "SOURCE_YOUTUBE",
    "RESERVED_SOURCE_IDS",
    "MEDIA_TYPES",
    "PRECISIONS",
    "PRECISION_SECOND",
    "PRECISION_MINUTE",
    "PRECISION_HOUR",
    "PRECISION_DAY",
    "PRECISION_UNKNOWN",
    # supporting value objects
    "Account",
    "Media",
    "Stats",
    "IMAGE",
    "VIDEO",
    "GIF",
]
