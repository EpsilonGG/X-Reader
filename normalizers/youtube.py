"""YouTube -> NormalizedItem.

Phase 2 established YouTube's place in the architecture and proved the frozen
contract absorbs it; Phase 4 adds the Provider and the Parser, so this
normalizer now runs on real data. The mapping is unchanged:

    video_id        -> item_id          (identity)
    channel         -> publisher
    title           -> title
    description     -> content
    thumbnail       -> media[0] (image)
    watch?v=<id>    -> canonical_url
    duration, view_count, channel_id -> metadata

Note that YouTube is the source that makes ``publisher`` obviously the right
name: a channel is not an author and not an account.

The identity namespace
----------------------
``source_id`` is ``"youtube:<channel_id>"``, not the bare ``"youtube"``. Two
reasons, and the second is the one that decided it:

* it follows the rule the RSS family already uses — a source is its own
  namespace, because "which source" is a real question the data has to answer;
* it makes multi-channel isolation structural rather than incidental. With one
  shared ``"youtube"`` namespace, two channels' videos land in the same file and
  are only separated by their video ids; with a namespace per channel they are
  separated by construction, exactly as two RSS sites are.

A video id is globally unique on YouTube, so the bare namespace would not have
collided — but "it happens not to collide" is a weaker guarantee than "it cannot
collide", and the RSS precedent already chose the stronger one. The channel id,
never the display name, is the key: a renamed channel must not fork its history.

``channel_id`` is still carried in ``metadata`` as well. It is redundant with
``source_id`` for the stored record, but it makes an item self-describing when
read in isolation, and it costs nothing.
"""
from __future__ import annotations

from domain.models.item import IMAGE
from domain.models.item import PRECISION_SECOND
from domain.models.item import PRECISION_UNKNOWN
from domain.models.item import SOURCE_YOUTUBE
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from domain.models.youtube_item import RawYouTubeItem
from normalizers.base import BaseNormalizer

WATCH_URL = "https://www.youtube.com/watch?v="


class YoutubeNormalizer(BaseNormalizer):
    """``RawYouTubeItem`` -> ``NormalizedItem``."""

    name = "youtube"
    #: Namespace *prefix*. The full ``source_id`` is ``youtube:<channel_id>``.
    source_id = SOURCE_YOUTUBE

    def normalize_one(self, raw: RawYouTubeItem) -> NormalizedItem | None:
        if not isinstance(raw, RawYouTubeItem):
            return None

        media: list[MediaItem] = []
        if raw.thumbnail_url:
            media.append(MediaItem(url=raw.thumbnail_url, type=IMAGE))

        return NormalizedItem(
            source_id=self.namespace(raw),
            item_id=raw.video_id,
            title=raw.title or None,
            content=raw.description or None,
            publisher=raw.channel or None,
            canonical_url=raw.url or f"{WATCH_URL}{raw.video_id}",
            published_at=raw.published_at,
            published_at_raw=raw.published_raw,
            published_precision=self._precision(raw),
            fetched_at=raw.fetched_at,
            media=media,
            metadata={
                "channel_id": raw.channel_id or None,
                "duration": raw.duration or None,
                "view_count": raw.view_count,
            },
        )

    @classmethod
    def namespace(cls, raw: RawYouTubeItem) -> str:
        """``youtube:<channel_id>``, falling back to ``youtube``.

        The fallback matters: a feed that lost its channel id must still produce
        an item with a *valid* identity, and ``"youtube:"`` with an empty tail
        would be a source id that looks populated and is not. Falling back to the
        bare namespace keeps the item addressable instead of unaddressable.
        """
        channel_id = (raw.channel_id or "").strip()
        return f"{SOURCE_YOUTUBE}:{channel_id}" if channel_id else SOURCE_YOUTUBE

    @staticmethod
    def _precision(raw: RawYouTubeItem) -> str:
        return raw.published_precision if raw.published_at else PRECISION_UNKNOWN
