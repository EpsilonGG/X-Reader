"""NormalizedItem — the cross-source content contract (Architecture Freeze v3).

STATUS: **formal domain contract, in force since Phase 2.**
It is produced by a Normalizer and persisted by Storage. The field list is
frozen: adding, removing or renaming a field is a contract change, not a
refactor.

What this model deliberately does not know about: providers, parsers, storage,
RSS, Telegram, QQ, Markdown, AI. It is a description of *content*, nothing else.
Anything that is a rendering or transport concern belongs downstream — for
example ``[from @username](url)`` is Telegram's markup and is assembled by a
renderer, never stored here.

Why this file exists
--------------------
Phase 1 modelled exactly one source (X) and therefore could afford one raw
record shape (``RawTweet``). Freeze v3 generalises that to "any source produces
a *Raw* record; a Normalizer turns it into a *NormalizedItem*". The design was
derived from three real inputs, not invented:

* **X** — ``RawTweet`` (this repo) plus the real Nitter markup in
  ``tests/fixtures/nitter_timeline.html``.
* **YouTube** — interface only; no real sample exists yet, so the model was
  checked against the minimum YouTube actually exposes (video_id, channel,
  title, description, published_at, thumbnail, url).
* **Web / RSS** — ``VisualNovel-Interview-RSS-main``, the real aggregator in this
  workspace: its ``models/item.py``, its 11 parsers, and its committed
  ``rss.xml`` (24 real items, copied verbatim to
  ``tests/fixtures/vnovel_rss_sample.xml``).

Design rules that shaped the field list
---------------------------------------
1. **No field is required merely because X has it.** ``author`` is the clearest
   case: the VNovel project's README advertises "作者" but its ``Item`` has no
   author field at all, and a website article often has no meaningful author.
   The general concept is therefore ``publisher``, and it is optional.
2. **Optional where the real data is optional.** In the real VNovel output only
   12 of 24 items carry a date and every X tweet has no title. ``title``,
   ``content``, ``published_at``, ``canonical_url`` and ``publisher`` are all
   therefore optional. A contract that demanded them would reject real data.
3. **Identity is derived, never stored.** ``identity_key`` is a property, not a
   field, so it cannot drift out of sync with ``source_id``/``item_id``.
4. **Metadata is an escape hatch with a promotion rule**, not a bin — see
   ``docs/ARCHITECTURE_FREEZE_V3.md`` section 10.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

# --- media vocabulary ------------------------------------------------------
# Closed set. Only members that a real source produces today are listed:
# X renders images, videos (with a poster frame) and looping gifs. "audio" is
# deliberately NOT reserved here -- see Decision Record DR-7.
IMAGE = "image"
VIDEO = "video"
GIF = "gif"

MEDIA_TYPES = (IMAGE, VIDEO, GIF)

# --- publication-time precision -------------------------------------------
# Required, because the real sources disagree and the difference is invisible
# in a bare timestamp: a Nitter item carries a second-precision date, whereas
# the VNovel GameWatch parser extracts only ``(YYYY/M/D)`` and the caller
# promotes it to midnight UTC. Without this field "published exactly at 00:00"
# and "published on that day" are indistinguishable.
PRECISION_SECOND = "second"
PRECISION_MINUTE = "minute"
PRECISION_HOUR = "hour"
PRECISION_DAY = "day"
PRECISION_UNKNOWN = "unknown"

PRECISIONS = (
    PRECISION_SECOND,
    PRECISION_MINUTE,
    PRECISION_HOUR,
    PRECISION_DAY,
    PRECISION_UNKNOWN,
)

# --- source namespaces ----------------------------------------------------
# ``source_id`` names *what the content is*, never *how it was reached*. The
# same X timeline can be fetched through the ``nitter`` or the ``xtf`` provider
# (see ``app/registry.py``); both must yield ``source_id="x"``, otherwise the
# same tweet would be stored twice under two identities.
SOURCE_X = "x"
SOURCE_YOUTUBE = "youtube"
#: Every other ``source_id`` is a web/RSS source and uses its own slug (the site
#: name, lowercased: ``"gamer"``, ``"gamebiz"``, ``"nookgaming"``, ...). Slugs
#: must not collide with a reserved name, so ``identity_key`` stays unambiguous.
#: A web site is its own namespace rather than one shared ``"web"`` namespace
#: because two sites can legitimately reuse the same numeric article id.
RESERVED_SOURCE_IDS = (SOURCE_X, SOURCE_YOUTUBE)


def build_identity_key(source_id: str, item_id: str) -> str:
    """The cross-source dedup key: ``"<source_id>:<item_id>"``.

    Exposed as a function as well as a property because a caller sometimes has
    to ask "have I seen this?" *before* it has a fully built item.
    """
    return f"{source_id}:{item_id}"


@dataclass(slots=True)
class MediaItem:
    """One attachment, described only as far as sources actually describe it.

    Kept close to the minimal shape the freeze suggested (``url``, ``type``,
    ``mime_type``, ``width``, ``height``) with exactly one addition, and the
    addition is evidence-driven: X renders a video as a ``<video>`` element plus
    a separate poster frame, so dropping ``thumbnail`` would silently lose real
    information that the Phase 1 model already captures
    (``domain/models/media.py``). No source in this workspace supplies
    ``width``/``height``, so they stay optional rather than removed -- RSS
    ``<enclosure>`` and the YouTube thumbnail API both can, and neither needs a
    new type.
    """

    url: str
    type: str = IMAGE
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    thumbnail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"url": self.url, "type": self.type}
        for key in ("mime_type", "width", "height", "thumbnail"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MediaItem:
        return cls(
            url=data.get("url", ""),
            type=data.get("type", IMAGE),
            mime_type=data.get("mime_type"),
            width=data.get("width"),
            height=data.get("height"),
            thumbnail=data.get("thumbnail"),
        )


@dataclass(slots=True)
class NormalizedItem:
    """One piece of content from any source, in one stable shape.

    Required (validated by :meth:`missing_required`): ``source_id``,
    ``item_id``, ``fetched_at``. Everything else is optional, because the real
    sources make it optional.

    An item additionally has to be *meaningful*: at least one of ``title`` or
    ``content`` must be non-empty. A record with neither carries no content and
    is rejected rather than stored.
    """

    # --- identity (required) ----------------------------------------------
    #: Namespace of the source: ``"x"``, ``"youtube"``, or a web site slug.
    source_id: str
    #: The source's own id for this item (tweet id, video id, feed guid).
    item_id: str

    # --- content (at least one required, see is_meaningful) ---------------
    #: None for X (a tweet has no title); the video title for YouTube; the
    #: article title for a web item.
    title: str | None = None
    #: The body/summary. For X this is the tweet text, for YouTube the
    #: description, for a web item the feed summary or article lead.
    content: str | None = None

    # --- who published it --------------------------------------------------
    #: The publishing entity *within* the source: ``@handle`` for X, channel
    #: for YouTube, site name for a web item. Optional because a malformed page
    #: can leave it unknown, but every current source normally supplies it.
    publisher: str | None = None

    # --- where it lives ----------------------------------------------------
    #: Permalink for humans. Optional in the type, but expected in practice:
    #: all three sources can produce one.
    canonical_url: str | None = None

    # --- when it was published (source's claim) ---------------------------
    #: ISO-8601, timezone-aware when the source was precise about it.
    published_at: str | None = None
    #: The source's own rendering, kept verbatim so a later Normalizer can
    #: re-interpret it without a refetch (same principle as
    #: ``RawTweet.created_at_raw``).
    published_at_raw: str = ""
    #: How much of ``published_at`` is real. See PRECISION_* above.
    published_precision: str = PRECISION_UNKNOWN

    # --- when *we* saw it (our claim) -------------------------------------
    #: When X-Reader observed the item. Never a substitute for published_at.
    fetched_at: str = ""

    # --- attachments -------------------------------------------------------
    media: list[MediaItem] = field(default_factory=list)

    # --- source-specific remainder ----------------------------------------
    #: Anything that is real but not (yet) part of the shared contract.
    #: Promotion rule in docs/ARCHITECTURE_FREEZE_V3.md section 10.
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- identity -----------------------------------------------------------
    @property
    def identity_key(self) -> str:
        """``"<source_id>:<item_id>"`` — the only dedup key.

        Derived rather than stored: a stored copy can disagree with its own
        components after an edit, a property cannot.
        """
        return build_identity_key(self.source_id, self.item_id)

    # -- validation ---------------------------------------------------------
    def missing_required(self) -> list[str]:
        """Names of required fields that are empty, in declaration order."""
        missing: list[str] = []
        if not (self.source_id or "").strip():
            missing.append("source_id")
        if not (self.item_id or "").strip():
            missing.append("item_id")
        if not (self.fetched_at or "").strip():
            missing.append("fetched_at")
        return missing

    def is_meaningful(self) -> bool:
        """True when the item carries at least some actual content.

        Deliberately *not* requiring ``canonical_url``: identity is
        ``source_id:item_id``, so an item without a URL is still perfectly
        deduplicable and still worth storing.
        """
        return bool((self.title or "").strip() or (self.content or "").strip())

    def is_valid(self) -> bool:
        return not self.missing_required() and self.is_meaningful()

    # -- serialisation ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """JSON-native mapping. ``identity_key`` is included for readers."""
        return {
            "identity_key": self.identity_key,
            "source_id": self.source_id,
            "item_id": self.item_id,
            "title": self.title,
            "content": self.content,
            "publisher": self.publisher,
            "canonical_url": self.canonical_url,
            "published_at": self.published_at,
            "published_at_raw": self.published_at_raw,
            "published_precision": self.published_precision,
            "fetched_at": self.fetched_at,
            "media": [m.to_dict() for m in self.media],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NormalizedItem:
        """Inverse of :meth:`to_dict`.

        ``identity_key`` is ignored on the way back in: it is derived from
        ``source_id``/``item_id``, and accepting a stored copy would let a
        corrupt line contradict itself.
        """
        return cls(
            source_id=data.get("source_id", ""),
            item_id=data.get("item_id", ""),
            title=data.get("title"),
            content=data.get("content"),
            publisher=data.get("publisher"),
            canonical_url=data.get("canonical_url"),
            published_at=data.get("published_at"),
            published_at_raw=data.get("published_at_raw", ""),
            published_precision=data.get("published_precision", PRECISION_UNKNOWN),
            fetched_at=data.get("fetched_at", ""),
            media=[MediaItem.from_dict(m) for m in (data.get("media") or [])],
            metadata=dict(data.get("metadata") or {}),
        )
