"""YouTube parser tests.

Everything here runs against a fixture that mirrors the real
``youtube.com/feeds/videos.xml`` document — ``yt:`` and ``media:`` namespaces,
``media:group`` payload, ``media:community`` statistics — because the whole point
of a dedicated YouTube parser is that the document is *not* a generic feed.

No test in this file touches the network. A parser that needed the network to be
tested would be a parser that had crossed its boundary.
"""
from __future__ import annotations

import pytest

from domain.errors import ParsingError
from domain.models.account import Account
from domain.models.item import PRECISION_SECOND
from domain.models.youtube_item import RawYouTubeItem
from parsers.youtube import YoutubeParser

from tests.conftest import YOUTUBE_CHANNEL_ID
from tests.conftest import YOUTUBE_OTHER_CHANNEL_ID


@pytest.fixture
def parser() -> YoutubeParser:
    return YoutubeParser()


# --- the happy path -------------------------------------------------------
def test_parses_every_entry(parser, youtube_feed_response, youtube_channel):
    videos = parser.parse(youtube_feed_response, youtube_channel)
    assert len(videos) == 3
    assert all(isinstance(video, RawYouTubeItem) for video in videos)
    assert [video.video_id for video in videos] == [
        "dQw4w9WgXcQ",
        "aaaaaaaaaaa",
        "bbbbbbbbbbb",
    ]


def test_video_id_is_read_from_the_yt_namespace(parser, youtube_feed_response, youtube_channel):
    """``<yt:videoId>`` is the identity, not the URL and not the title."""
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.video_id == "dQw4w9WgXcQ"


def test_title_and_description_come_from_the_media_group(
    parser, youtube_feed_response, youtube_channel
):
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.title == "First Video"
    # ``&amp;`` in the document must arrive decoded, not doubled.
    assert first.description == "First video description & more."


def test_published_becomes_a_utc_timestamp_at_second_precision(
    parser, youtube_feed_response, youtube_channel
):
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.published_at == "2026-09-28T11:45:00+00:00"
    assert first.published_precision == PRECISION_SECOND
    assert first.published_raw == "2026-09-28T11:45:00+00:00"


def test_updated_is_kept_as_evidence_and_never_used_as_published(
    parser, youtube_feed_response, youtube_channel
):
    """The contract has no ``updated_at`` field (DR-4), so it must not leak into
    ``published_at`` — but dropping it would destroy unrefetchable information."""
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.raw["updated"] == "2026-09-29T09:00:00+00:00"
    assert first.published_at == "2026-09-28T11:45:00+00:00"


def test_thumbnail_duration_and_views(parser, youtube_feed_response, youtube_channel):
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.thumbnail_url == "https://i4.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    assert first.duration == "213"
    assert first.view_count == 12345


def test_channel_name_and_channel_id(parser, youtube_feed_response, youtube_channel):
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.channel == "Some Channel"
    assert first.channel_id == YOUTUBE_CHANNEL_ID


def test_canonical_watch_url_comes_from_the_alternate_link(
    parser, youtube_feed_response, youtube_channel
):
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_fetched_at_is_the_response_time(parser, youtube_feed_response, youtube_channel):
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.fetched_at == youtube_feed_response.fetched_at.isoformat()


def test_raw_evidence_is_kept(parser, youtube_feed_response, youtube_channel):
    first = parser.parse(youtube_feed_response, youtube_channel)[0]
    assert first.raw["video_id"] == "dQw4w9WgXcQ"
    assert first.raw["views"] == 12345


# --- partial data ---------------------------------------------------------
def test_an_empty_description_is_kept_as_empty_not_as_the_title(
    parser, youtube_feed_response, youtube_channel
):
    third = parser.parse(youtube_feed_response, youtube_channel)[2]
    assert third.description == ""
    # The title must not be substituted: the item is still meaningful because it
    # has a title, and inventing content would be worse than reporting none.
    assert third.title == "Third Video Without Description Or Thumbnail"


def test_a_missing_thumbnail_leaves_media_empty(parser, youtube_feed_response, youtube_channel):
    third = parser.parse(youtube_feed_response, youtube_channel)[2]
    assert third.thumbnail_url == ""


def test_a_missing_video_id_drops_only_that_entry(parser, youtube_channel):
    body = _feed(
        """
        <entry>
          <id>yt:video:keepme12345</id>
          <yt:videoId>keepme12345</yt:videoId>
          <title>Kept</title>
          <media:group><media:description>d</media:description></media:group>
        </entry>
        <entry>
          <title>No identity at all</title>
          <media:group><media:description>d</media:description></media:group>
        </entry>
        """
    )
    videos = parser.parse(_response(body), youtube_channel)
    assert [video.video_id for video in videos] == ["keepme12345"]


def test_the_id_element_is_used_when_yt_videoid_is_absent(parser, youtube_channel):
    body = _feed(
        """
        <entry>
          <id>yt:video:fallback1234</id>
          <title>Fallback</title>
          <media:group><media:description>d</media:description></media:group>
        </entry>
        """
    )
    videos = parser.parse(_response(body), youtube_channel)
    assert videos[0].video_id == "fallback1234"


def test_a_channel_level_id_is_never_mistaken_for_a_video_id(parser, youtube_channel):
    """The feed-level ``yt:channel:...`` id must not become an item identity."""
    body = _feed(
        """
        <entry>
          <id>yt:channel:UCabcdefghijklmnopqrstuv</id>
          <title>Only a title</title>
          <media:group><media:description>d</media:description></media:group>
        </entry>
        """
    )
    assert parser.parse(_response(body), youtube_channel) == []


def test_an_entry_with_no_title_and_no_description_is_not_a_content_item(
    parser, youtube_channel
):
    body = _feed(
        """
        <entry>
          <id>yt:video:emptybody12</id>
          <yt:videoId>emptybody12</yt:videoId>
        </entry>
        """
    )
    assert parser.parse(_response(body), youtube_channel) == []


def test_one_malformed_entry_does_not_kill_the_feed(parser, youtube_channel):
    body = _feed(
        """
        <entry>
          <yt:videoId>good1aaaaaaa</yt:videoId>
          <title>Good</title>
          <media:group><media:statistics views="not-a-number"/></media:group>
        </entry>
        <entry>
          <yt:videoId>good2aaaaaaa</yt:videoId>
          <title>Also good</title>
          <media:group><media:description>d</media:description></media:group>
        </entry>
        """
    )
    videos = parser.parse(_response(body), youtube_channel)
    assert [video.video_id for video in videos] == ["good1aaaaaaa", "good2aaaaaaa"]
    # A non-numeric view count is dropped to None rather than raising.
    assert videos[0].view_count is None


# --- namespaces -----------------------------------------------------------
def test_a_bare_atom_feed_without_the_yt_namespace_is_still_read(parser, youtube_channel):
    """Some mirrors omit ``xmlns:yt``; the ``<id>`` fallback must still work."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:media="http://search.yahoo.com/mrss/">'
        "<title>Mirror</title>"
        "<entry>"
        "<id>yt:video:mirror12345</id>"
        "<title>Mirrored</title>"
        "<media:group><media:description>d</media:description></media:group>"
        "</entry>"
        "</feed>"
    )
    videos = parser.parse(_response(body), youtube_channel)
    assert videos[0].video_id == "mirror12345"
    assert videos[0].channel == "Mirror"


def test_the_feed_level_channel_id_is_the_fallback(parser, youtube_feed_response, youtube_channel):
    """An entry that omits ``yt:channelId`` still gets the channel's identity."""
    body = _feed(
        """
        <entry>
          <yt:videoId>nochannelid1</yt:videoId>
          <title>No per-entry channel id</title>
          <media:group><media:description>d</media:description></media:group>
        </entry>
        """
    )
    videos = parser.parse(_response(body), youtube_channel)
    assert videos[0].channel_id == YOUTUBE_CHANNEL_ID


def test_the_tracked_unit_is_the_last_resort_for_the_channel_id(parser):
    """Even a feed with no channel id at all is attributed to the tracked unit."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns:media="http://search.yahoo.com/mrss/" '
        'xmlns="http://www.w3.org/2005/Atom">'
        "<title>Some Channel</title>"
        "<entry>"
        "<yt:videoId>barefeed1234</yt:videoId>"
        "<title>Bare</title>"
        "<media:group><media:description>d</media:description></media:group>"
        "</entry>"
        "</feed>"
    )
    videos = parser.parse(_response(body), Account(username="UCfallback0000000000000"))
    assert videos[0].channel_id == "UCfallback0000000000000"


# --- failures -------------------------------------------------------------
def test_invalid_xml_is_a_parsing_error(parser, make_response, youtube_channel):
    response = make_response(
        "<feed><entry>", url="https://www.youtube.com/feeds/videos.xml", content_type="application/atom+xml"
    )
    with pytest.raises(ParsingError) as excinfo:
        parser.parse(response, youtube_channel)
    assert "not well-formed" in str(excinfo.value)


def test_xml_that_is_not_a_feed_is_a_parsing_error(parser, make_response, youtube_channel):
    response = make_response("<html><body>consent</body></html>", url="https://www.youtube.com/")
    with pytest.raises(ParsingError) as excinfo:
        parser.parse(response, youtube_channel)
    assert "not a YouTube channel feed" in str(excinfo.value)


def test_an_empty_body_is_a_parsing_error(parser, make_response, youtube_channel):
    response = make_response("", url="https://www.youtube.com/feeds/videos.xml")
    with pytest.raises(ParsingError):
        parser.parse(response, youtube_channel)


def test_a_feed_with_no_entries_is_an_empty_list_not_an_error(
    parser, make_response, youtube_channel
):
    """A channel with no uploads is normal, not a failure."""
    response = make_response(
        _feed(""),
        url="https://www.youtube.com/feeds/videos.xml",
        content_type="application/atom+xml",
    )
    assert parser.parse(response, youtube_channel) == []


# --- multi-channel --------------------------------------------------------
def test_a_second_channel_parses_to_its_own_channel_id(
    parser, youtube_other_feed_response
):
    videos = parser.parse(
        youtube_other_feed_response, Account(username=YOUTUBE_OTHER_CHANNEL_ID)
    )
    assert [video.channel_id for video in videos] == [YOUTUBE_OTHER_CHANNEL_ID]
    assert videos[0].channel == "Another Channel"


def test_the_two_channels_share_no_video_ids(
    parser, youtube_feed_response, youtube_other_feed_response, youtube_channel
):
    first = {v.video_id for v in parser.parse(youtube_feed_response, youtube_channel)}
    second = {
        v.video_id
        for v in parser.parse(
            youtube_other_feed_response, Account(username=YOUTUBE_OTHER_CHANNEL_ID)
        )
    }
    assert first and second
    assert first.isdisjoint(second)


# --- helpers --------------------------------------------------------------
def _feed(entries: str) -> str:
    """A minimal but namespace-correct channel feed around ``entries``."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns:media="http://search.yahoo.com/mrss/" '
        'xmlns="http://www.w3.org/2005/Atom">'
        f"<id>yt:channel:{YOUTUBE_CHANNEL_ID}</id>"
        f"<yt:channelId>{YOUTUBE_CHANNEL_ID}</yt:channelId>"
        "<title>Some Channel</title>"
        "<author><name>Some Channel</name></author>"
        f"{entries}"
        "</feed>"
    )


def _response(text: str):
    from datetime import datetime
    from datetime import timezone

    from infrastructure.http.response import RawResponse

    return RawResponse(
        url="https://www.youtube.com/feeds/videos.xml",
        status_code=200,
        content_type="application/atom+xml",
        text=text,
        fetched_at=datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc),
    )
