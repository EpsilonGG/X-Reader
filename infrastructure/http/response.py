"""The raw response contract between Fetchers and Parsers.

Shape inherited verbatim from X-rss (`infrastructure/http/response.py`), which
already proved this is the right boundary: the fetcher hands over *bytes plus
provenance* and knows nothing about tweets.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class RawResponse:
    """An untouched upstream response."""

    url: str
    status_code: int
    content_type: str
    text: str
    fetched_at: datetime

    @property
    def size_bytes(self) -> int:
        return len(self.text.encode("utf-8", errors="replace"))

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()
