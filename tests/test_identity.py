"""Identity and timestamp helpers: tweet ids, permalinks, dates, media URLs.

These are the small pure functions that everything else depends on, so they get
their own focused tests. A bug here corrupts the whole store quietly, which is
worse than a crash.
"""
from __future__ import annotations

import pytest

from parsers.base import BaseParser
from parsers.media_url import resolve_media_url
from parsers.timeutil import is_absolute_date
from parsers.timeutil import parse_source_date


# --- tweet ids ------------------------------------------------------------
@pytest.mark.parametrize(
    "link,expected",
    [
        ("/jack/status/1000000000000000001#m", "1000000000000000001"),
        ("/jack/status/1000000000000000001", "1000000000000000001"),
        ("https://nitter.example/jack/status/123#m", "123"),
        ("https://x.com/jack/status/123?s=20&t=abc", "123"),
        ("https://x.com/jack/status/123/photo/1", "123"),
        ("/jack/status/123#m", "123"),
        ("", ""),
        ("/jack", ""),
        ("https://nitter.example/jack/rss", ""),
        ("not a link at all", ""),
    ],
)
def test_tweet_id_extraction(link, expected):
    assert BaseParser.tweet_id_from_link(link) == expected


@pytest.mark.parametrize(
    "link,expected",
    [
        ("/jack/status/123#m", "jack"),
        ("https://x.com/OpenAI/status/456", "OpenAI"),
        ("https://nitter.example/SCA_DI/status/789?s=1", "SCA_DI"),
        ("", ""),
        ("https://nitter.example/jack/rss", ""),
    ],
)
def test_username_extraction(link, expected):
    assert BaseParser.username_from_link(link) == expected


def test_canonical_url_is_always_an_x_permalink():
    """Storing the canonical URL at parse time removes X-rss's regex rewrite of
    the channel title in its processor stage."""
    assert (
        BaseParser.canonical_url("jack", "123")
        == "https://x.com/jack/status/123"
    )


# --- dates ----------------------------------------------------------------
def test_rfc2822_date_parsed_to_utc_iso():
    assert parse_source_date("Wed, 11 Sep 2024 19:31:00 GMT") == (
        "2024-09-11T19:31:00+00:00"
    )


def test_nitter_title_date_parsed():
    assert parse_source_date("Sep 11, 2024 · 7:31 PM UTC") == (
        "2024-09-11T19:31:00+00:00"
    )


def test_single_digit_day_parsed():
    assert parse_source_date("Sep 5, 2024 · 8:15 AM UTC") == (
        "2024-09-05T08:15:00+00:00"
    )


def test_full_month_name_parsed():
    assert parse_source_date("September 11, 2024 · 7:31 PM UTC") == (
        "2024-09-11T19:31:00+00:00"
    )


def test_iso_date_parsed_including_trailing_z():
    assert parse_source_date("2024-09-11T19:31:00Z") == "2024-09-11T19:31:00+00:00"


@pytest.mark.parametrize("value", ["", "   ", "3h", "Jul 3", "now", "not a date"])
def test_relative_or_unknown_dates_return_none(value):
    """Never fabricate an absolute time from a relative one."""
    assert parse_source_date(value) is None


def test_is_absolute_date_agrees_with_parse():
    assert is_absolute_date("Sep 11, 2024 · 7:31 PM UTC") is True
    assert is_absolute_date("3h") is False


# --- media URLs -----------------------------------------------------------
@pytest.mark.parametrize(
    "href,expected",
    [
        # Images: the `orig/` segment means "full size".
        (
            "/pic/orig/media%2FCNq2BQMWIAESvuO.jpg",
            "https://pbs.twimg.com/media/CNq2BQMWIAESvuO.jpg",
        ),
        (
            "/pic/media%2FCNq2BQMWIAESvuO.jpg",
            "https://pbs.twimg.com/media/CNq2BQMWIAESvuO.jpg",
        ),
        # Videos: the decoded path already carries a host, which must be kept.
        (
            "/pic/video.twimg.com%2Ftweet_video%2FHD-x.mp4",
            "https://video.twimg.com/tweet_video/HD-x.mp4",
        ),
        (
            "/pic/pbs.twimg.com%2Famplify_video_thumb%2FT.jpg",
            "https://pbs.twimg.com/amplify_video_thumb/T.jpg",
        ),
        # Absolute Nitter proxy URLs (what RSS enclosures look like).
        (
            "https://nitter.example/pic/media%2FSAME.jpg",
            "https://pbs.twimg.com/media/SAME.jpg",
        ),
        # Already-real URLs pass through untouched.
        (
            "https://pbs.twimg.com/media/ALREADY.jpg",
            "https://pbs.twimg.com/media/ALREADY.jpg",
        ),
        ("", ""),
    ],
)
def test_media_url_resolution(href, expected):
    assert resolve_media_url(href) == expected


def test_media_url_resolution_never_prefixes_pbs_onto_a_video_host():
    """The naive implementation yields pbs.twimg.com/video.twimg.com/... ."""
    resolved = resolve_media_url("/pic/video.twimg.com%2Ftweet_video%2FHD-x.mp4")
    assert resolved.count("pbs.twimg.com") == 0
