"""RawYouTubeItem — the raw record for the YouTube source.

Phase 2 scope (task section 15): **interface and minimal contract only.**
There is no YouTube provider and no fetch implementation here, because every
no-API route to YouTube data needs a decision the operator has not made yet
(channel RSS vs playlist feed vs yt-dlp vs an API key), and the task forbids
introducing the official API or a third-party service to "finish" Phase 2.

What this file therefore establishes is only that YouTube has a defined place in
the architecture::

    YouTube (future Provider) -> RawYouTubeItem -> YoutubeNormalizer
                                                      |
                                                      v
                                                NormalizedItem

The field list is exactly the minimum YouTube exposes for a video, and nothing
speculative. ``tests/test_normalized_item_contract.py`` already proves this set
maps onto the frozen contract with no new fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass(slots=True)
class RawYouTubeItem:
    """One video, before normalization."""

    # --- identity -----------------------------------------------------------
    #: The 11-character YouTube video id. This *is* the identity.
    video_id: str

    # --- content ------------------------------------------------------------
    title: str = ""
    description: str = ""

    # --- publisher ----------------------------------------------------------
    channel: str = ""
    channel_id: str = ""

    # --- publication time ---------------------------------------------------
    published_raw: str = ""
    published_at: str | None = None
    published_precision: str = "unknown"

    # --- media / url --------------------------------------------------------
    thumbnail_url: str = ""
    url: str = ""

    # --- extras -------------------------------------------------------------
    duration: str = ""
    view_count: int | None = None

    # --- observation --------------------------------------------------------
    fetched_at: str = ""

    # --- evidence -----------------------------------------------------------
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "description": self.description,
            "channel": self.channel,
            "channel_id": self.channel_id,
            "published_raw": self.published_raw,
            "published_at": self.published_at,
            "published_precision": self.published_precision,
            "thumbnail_url": self.thumbnail_url,
            "url": self.url,
            "duration": self.duration,
            "view_count": self.view_count,
            "fetched_at": self.fetched_at,
            "raw": self.raw,
        }
