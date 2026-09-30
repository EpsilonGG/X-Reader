"""RSS / Atom -> NormalizedItem.

Generic by design. X-Reader supports *any* feed whose URL is declared in
``rss_sources`` (``config/config.yaml``); it has no VisualNovel-specific provider
or parser and imports nothing from ``VisualNovel-Interview-RSS-main``. That
project's role is to emit standard RSS — so does any other tool.

Identity (freeze section 6, task section 8)
-------------------------------------------
``source_id`` comes from the RSS source config id, so two feeds can never share
an identity space. ``item_id`` follows a strict priority:

1. ``<guid>`` / ``<id>`` — **but only when it is independent of the link**.
   Real feeds very often set ``guid`` to the URL, which is not an identifier at
   all; the VNovel feed does exactly this for 24 of 24 items.
2. otherwise the canonical URL, reduced to its last path segment.

``metadata["identity_basis"]`` records which of the two was used, so the weaker
case is visible in stored data instead of being silently equivalent.

Never used as identity: the title, the publication time, or a hash of the
content. All three change without the item changing.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from domain.models.item import GIF
from domain.models.item import IMAGE
from domain.models.item import VIDEO
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from domain.models.rss_item import RawRSSItem
from normalizers.base import BaseNormalizer

#: Extensions used to guess a media kind when the feed omits the mime type.
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp")
_VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")
_GIF_EXT = (".gif",)


def item_id_from_url(url: str) -> str:
    """Reduce a URL to a stable slug for identity purposes.

    The fragment is dropped and the query is dropped: both are overwhelmingly
    tracking noise (``?ref=``, ``#m``), and keeping them would make the same
    article look new every time a feed changed its link decoration.

    Accepted cost: two pages that differ *only* by a meaningful query parameter
    collapse into one item. Documented rather than hidden — see
    ``docs/ARCHITECTURE_FREEZE_V3.md`` risk R2.
    """
    if not url:
        return ""
    path = urlsplit(url).path.rstrip("/")
    if not path:
        # No path at all: the URL *is* the identity. Returning the last dotted
        # fragment here would reduce `https://s.example/` to `"s"`, and two
        # different hosts could then collide.
        return url.split("#", 1)[0]
    tail = path.split("/")[-1]
    # A trailing ".html" / ".htm" carries no identity; a trailing ".php" or an
    # id-like extension is stripped too, which is the common case.
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    return stem or path


class RssNormalizer(BaseNormalizer):
    """``RawRSSItem`` -> ``NormalizedItem``."""

    name = "rss"

    def normalize_one(self, raw: RawRSSItem) -> NormalizedItem | None:
        if not isinstance(raw, RawRSSItem):
            return None

        item_id, basis = self._identity(raw)
        media, unmapped = self._media(raw)
        metadata: dict = {"identity_basis": basis}
        if raw.categories:
            metadata["categories"] = list(raw.categories)
        if unmapped:
            # An enclosure whose kind the frozen media vocabulary cannot express
            # (audio, or an unknown mime). Kept, never mislabelled as an image.
            metadata["enclosure"] = unmapped
        if raw.updated_raw and raw.updated_raw != raw.published_raw:
            # DR-4: the contract has no updated_at field, so this stays evidence.
            metadata["updated_raw"] = raw.updated_raw

        return NormalizedItem(
            source_id=raw.source_id,
            item_id=item_id,
            title=raw.title or None,
            content=self._content(raw),
            publisher=self._publisher(raw),
            canonical_url=raw.link or None,
            published_at=raw.published_at,
            published_at_raw=raw.published_raw,
            published_precision=raw.published_precision,
            fetched_at=raw.fetched_at,
            media=media,
            metadata=metadata,
        )

    # -- pieces -------------------------------------------------------------
    @staticmethod
    def _identity(raw: RawRSSItem) -> tuple[str, str]:
        guid = (raw.item_id_raw or "").strip()
        link = (raw.link or "").strip()
        # A guid that merely repeats the link is not an identifier.
        if guid and guid != link:
            return guid, "guid"
        return item_id_from_url(link), "url"

    @staticmethod
    def _content(raw: RawRSSItem) -> str | None:
        """``content:encoded`` when the feed has it, else ``<description>``."""
        return raw.content or raw.summary or None

    @staticmethod
    def _publisher(raw: RawRSSItem) -> str | None:
        """The feed's own name for the source, else the item author.

        Deliberately **not** derived from a bracketed title prefix such as
        ``[Gamer] Title``. That convention belongs to one reference project's
        renderer, and parsing it generically would corrupt legitimate titles
        that start with a bracket (``[重要] ...``). A feed that wants to name its
        origin should use the standard RSS ``<source>`` element instead — see
        Decision Record DR-15.
        """
        return raw.feed_title or raw.author or None

    @staticmethod
    def _media(raw: RawRSSItem) -> tuple[list[MediaItem], dict | None]:
        url = (raw.enclosure_url or "").strip()
        if not url:
            return [], None
        mime = (raw.enclosure_type or "").strip().lower()
        lowered = url.lower()

        if mime.startswith("image/") or (not mime and lowered.endswith(_IMAGE_EXT)):
            kind = GIF if lowered.endswith(_GIF_EXT) else IMAGE
            return [MediaItem(url=url, type=kind, mime_type=mime or None)], None
        if mime.startswith("video/") or (not mime and lowered.endswith(_VIDEO_EXT)):
            return [MediaItem(url=url, type=VIDEO, mime_type=mime or None)], None
        # audio/*, application/*, or anything unrecognised: keep the evidence,
        # do not force it into a vocabulary that cannot describe it.
        return [], {"url": url, "type": mime or None, "length": raw.enclosure_length or None}
