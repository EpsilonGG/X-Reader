"""Shared test fixtures.

The project uses absolute imports rooted at the project directory (the same
convention as X-rss), so the root is put on ``sys.path`` here. ``pyproject.toml``
also declares ``pythonpath = ["."]``, but doing it in ``conftest`` means the
suite runs under a bare ``pytest`` invocation too.
"""
from __future__ import annotations

import sys
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain.models.account import Account  # noqa: E402
from infrastructure.http.response import RawResponse  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: A fixed instant so timestamps in assertions are deterministic.
FROZEN = datetime(2024, 9, 11, 19, 31, 0, tzinfo=timezone.utc)


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def account() -> Account:
    return Account(username="jack")


@pytest.fixture
def make_response():
    """Build a :class:`RawResponse` from fixture text."""

    def _make(
        text: str,
        *,
        url: str = "https://nitter.example/jack",
        status_code: int = 200,
        content_type: str = "text/html",
        fetched_at: datetime | None = None,
    ) -> RawResponse:
        return RawResponse(
            url=url,
            status_code=status_code,
            content_type=content_type,
            text=text,
            fetched_at=fetched_at or FROZEN,
        )

    return _make


@pytest.fixture
def timeline_response(make_response) -> RawResponse:
    return make_response(fixture_text("nitter_timeline.html"))


@pytest.fixture
def feed_response(make_response) -> RawResponse:
    return make_response(
        fixture_text("nitter_feed.xml"),
        url="https://nitter.example/jack/rss",
        content_type="application/rss+xml",
    )


#: The channel id used by ``youtube_channel_feed.xml``. Kept here so a test does
#: not have to re-state it and risk a typo that makes an assertion vacuous.
YOUTUBE_CHANNEL_ID = "UCabcdefghijklmnopqrstuv"
YOUTUBE_OTHER_CHANNEL_ID = "UCzyxwvutsrqponmlkjihgfe"


def youtube_feed_url(channel_id: str = YOUTUBE_CHANNEL_ID) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


@pytest.fixture
def youtube_channel() -> Account:
    """The tracked unit for a YouTube channel: its ``channel_id``."""
    return Account(username=YOUTUBE_CHANNEL_ID)


@pytest.fixture
def youtube_feed_response(make_response) -> RawResponse:
    return make_response(
        fixture_text("youtube_channel_feed.xml"),
        url=youtube_feed_url(),
        content_type="application/atom+xml",
    )


@pytest.fixture
def youtube_other_feed_response(make_response) -> RawResponse:
    return make_response(
        fixture_text("youtube_channel_feed_other.xml"),
        url=youtube_feed_url(YOUTUBE_OTHER_CHANNEL_ID),
        content_type="application/atom+xml",
    )
