"""Media attached to a tweet.

Only the shapes that Nitter actually renders are modelled (verified against
real Nitter markup): an image, a video (with poster thumbnail), or a looping
gif. Nothing speculative is added here.
"""
from __future__ import annotations

from dataclasses import dataclass

IMAGE = "image"
VIDEO = "video"
GIF = "gif"


@dataclass(slots=True)
class Media:
    """One media attachment, resolved to its real twimg URL."""

    url: str
    type: str = IMAGE
    thumbnail: str | None = None

    def to_dict(self) -> dict:
        data = {"url": self.url, "type": self.type}
        if self.thumbnail:
            data["thumbnail"] = self.thumbnail
        return data
