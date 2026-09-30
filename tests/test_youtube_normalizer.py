"""YouTube normalizer tests.

The normalizer is the only component that knows both YouTube's raw shape and the
shared contract. These tests assert the two things that matter: that a YouTube
video becomes a *standard* ``NormalizedItem`` with no YouTube-specific type or
field, and that its identity is stable across everything that can change
(a display name, a re-fetch, a second channel reusing a video id).
"""
from __future__ import annotations

from domain.models.item import IMAGE
from domain.models.item import PRECISION_SECOND
from domain.models.item import PRECISION_UNKNOWN
from domain.models.item import SOURCE_YOUTUBE
from domain.models.item import NormalizedItem
from domain.models.tweet import RawTweet
from domain.models.youtube_item import RawYouTubeItem
from normalizers.youtube import YoutubeNormalizer

from tests.conftest import YOUTUBE_CHANNEL_ID
from tests.conftest import YOUTUBE_OTHER_CHANNEL_ID

FETCHED = "2026-09-28T12:00:00+00:00"


def video(**overrides) -> RawYouTubeItem:
    data = dict(
        video_id="dQw4w9WgXcQ",
        title="A video",
        description="A description",
        channel="Some Channel",
        channel_id="UC123",
        published_raw="2026-09-28T11:45:00+00:00",
        published_at="2026-09-28T11:45:00+00:00",
        published_precision=PRECISION_SECOND,
        thumbnail_url="https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        duration="213",
        view_count=1000,
        fetched_at=FETCHED,
        raw={},
    )
    data.update(overrides)
    return RawYouTubeItem(**data)


def normalized(**overrides) -> NormalizedItem:
    item = YoutubeNormalizer().normalize_one(video(**overrides))
    assert item is not None
    return item


# --- identity -------------------------------------------------------------
def test_video_id_is_the_item_id():
    assert normalized().item_id == "dQw4w9WgXcQ"


def test_the_namespace_is_the_channel_not_the_bare_source_name():
    assert normalized().source_id == f"{SOURCE_YOUTUBE}:UC123"


def test_identity_key_is_source_id_colon_item_id():
    assert normalized().identity_key == f"{SOURCE_YOUTUBE}:UC123:dQw4w9WgXcQ"


def test_the_namespace_falls_back_when_the_channel_id_is_missing():
    """``"youtube:"`` would be a source id that looks populated and is not."""
    item = normalized(channel_id="")
    assert item.source_id == SOURCE_YOUTUBE
    assert item.identity_key == f"{SOURCE_YOUTUBE}:dQw4w9WgXcQ"


def test_source_id_does_not_change_when_the_display_name_changes():
    """A renamed channel must not fork its history."""
    before = normalized(channel="Old Name")
    after = normalized(channel="A Completely New Name")
    assert before.source_id == after.source_id
    assert before.identity_key == after.identity_key


def test_identity_key_is_stable_across_a_refetch():
    first = normalized()
    second = normalized(fetched_at="2027-01-01T00:00:00+00:00")
    assert first.identity_key == second.identity_key


def test_the_same_video_twice_is_one_identity():
    """What Storage relies on to deduplicate a repeated feed entry."""
    assert normalized().identity_key == normalized().identity_key


def test_the_same_video_id_in_two_channels_stays_two_identities():
    """Multi-channel isolation: the namespace, not the video id, separates them."""
    a = normalized(channel_id=YOUTUBE_CHANNEL_ID)
    b = normalized(channel_id=YOUTUBE_OTHER_CHANNEL_ID)
    assert a.item_id == b.item_id
    assert a.source_id != b.source_id
    assert a.identity_key != b.identity_key


# --- content --------------------------------------------------------------
def test_title_becomes_title_and_description_becomes_content():
    item = normalized()
    assert item.title == "A video"
    assert item.content == "A description"


def test_an_empty_description_becomes_no_content_rather_than_an_empty_string():
    item = normalized(description="")
    assert item.content is None
    assert item.title == "A video"
    assert item.is_valid()


def test_a_video_with_neither_title_nor_description_is_not_valid():
    assert not normalized(title="", description="").is_valid()


def test_the_channel_becomes_the_publisher():
    assert normalized().publisher == "Some Channel"


def test_a_missing_channel_becomes_no_publisher():
    assert normalized(channel="").publisher is None


# --- url ------------------------------------------------------------------
def test_the_watch_url_from_the_feed_is_kept():
    assert normalized().canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_the_watch_url_is_built_when_the_feed_gave_none():
    assert (
        normalized(url="").canonical_url
        == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )


# --- media ----------------------------------------------------------------
def test_the_thumbnail_becomes_one_image_media_item():
    item = normalized()
    assert len(item.media) == 1
    assert item.media[0].type == IMAGE
    assert item.media[0].url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq.jpg"


def test_a_missing_thumbnail_yields_no_media_and_is_still_valid():
    item = normalized(thumbnail_url="")
    assert item.media == []
    assert item.is_valid()


# --- time -----------------------------------------------------------------
def test_published_time_and_precision_are_carried_through():
    item = normalized()
    assert item.published_at == "2026-09-28T11:45:00+00:00"
    assert item.published_at_raw == "2026-09-28T11:45:00+00:00"
    assert item.published_precision == PRECISION_SECOND


def test_no_published_time_means_unknown_precision():
    item = normalized(published_at=None, published_raw="", published_precision=PRECISION_SECOND)
    assert item.published_precision == PRECISION_UNKNOWN


def test_fetched_at_is_never_a_substitute_for_published_at():
    item = normalized(published_at=None, published_raw="")
    assert item.fetched_at == FETCHED
    assert item.published_at is None


# --- metadata -------------------------------------------------------------
def test_duration_views_and_channel_id_stay_in_metadata():
    """Nothing YouTube-specific was promoted into the contract."""
    metadata = normalized().metadata
    assert metadata["duration"] == "213"
    assert metadata["view_count"] == 1000
    assert metadata["channel_id"] == "UC123"


# --- the contract itself --------------------------------------------------
def test_the_result_is_a_plain_normalized_item():
    item = normalized()
    assert isinstance(item, NormalizedItem)
    assert type(item) is NormalizedItem


def test_a_foreign_raw_record_is_discarded():
    foreign = RawTweet(tweet_id="1", account="jack", url="https://x.com/jack/status/1")
    assert YoutubeNormalizer().normalize_one(foreign) is None


def test_normalize_drops_records_that_cannot_be_valid():
    """A batch is filtered by the shared validity gate, not by a YouTube rule."""
    items = YoutubeNormalizer().normalize(
        [video(), video(video_id="", title="", description="")]
    )
    assert len(items) == 1
    assert items[0].item_id == "dQw4w9WgXcQ"
