"""Normalizer tests — one section per kind.

A Normalizer is the only component that knows both a source's raw shape and the
unified contract, and it is a pure function. These tests therefore need no
network, no storage and no fixtures on disk beyond the raw records themselves.

What each section is really checking:

* **X** — that a tweet's text becomes ``content`` (not ``title``), that a missing
  ``created_at`` is never invented, and that no Telegram markup leaks in.
* **RSS** — that identity comes from ``guid`` when it is a real identifier and
  from the URL when it is not, and that the two cases are distinguishable in
  stored data.
* **YouTube** — that the frozen contract absorbs a source with a channel, a
  title and a thumbnail, which is a different shape from both of the others.
"""
from __future__ import annotations

import pytest

from domain.models.item import GIF
from domain.models.item import IMAGE
from domain.models.item import PRECISION_DAY
from domain.models.item import PRECISION_SECOND
from domain.models.item import PRECISION_UNKNOWN
from domain.models.item import SOURCE_X
from domain.models.item import SOURCE_YOUTUBE
from domain.models.item import VIDEO
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from domain.models.media import Media
from domain.models.rss_item import RawRSSItem
from domain.models.tweet import RawTweet
from domain.models.tweet import Stats
from domain.models.youtube_item import RawYouTubeItem
from normalizers.registry import KIND_RSS
from normalizers.registry import KIND_X
from normalizers.registry import KIND_YOUTUBE
from normalizers.registry import kinds
from normalizers.registry import normalizer_for
from normalizers.rss import RssNormalizer
from normalizers.rss import item_id_from_url
from normalizers.x import XNormalizer
from normalizers.youtube import YoutubeNormalizer

FETCHED = "2024-09-11T19:31:00+00:00"


def tweet(**overrides) -> RawTweet:
    data = dict(
        tweet_id="42",
        account="jack",
        url="https://x.com/jack/status/42",
        text="hello world",
        created_at="2024-09-11T19:31:00+00:00",
        created_at_raw="Sep 11, 2024 · 7:31 PM UTC",
        author_username="jack",
        author_name="Jack",
        provider="nitter",
        route="html",
        fetched_at=FETCHED,
        raw={"source": "nitter_html"},
    )
    data.update(overrides)
    return RawTweet(**data)


def feed_item(**overrides) -> RawRSSItem:
    data = dict(
        source_id="vnovel",
        item_id_raw="",
        title="A title",
        summary="A summary",
        content="",
        link="https://example.com/article/1234",
        published_raw="Mon, 28 Sep 2026 11:45:00 +0000",
        published_at="2026-09-28T11:45:00+00:00",
        published_precision=PRECISION_SECOND,
        author="",
        feed_title="Example",
        enclosure_url="",
        enclosure_type="",
        enclosure_length="",
        categories=[],
        updated_raw="",
        fetched_at=FETCHED,
        raw={},
    )
    data.update(overrides)
    return RawRSSItem(**data)


# --- the registry ---------------------------------------------------------
def test_registry_exposes_every_kind():
    assert kinds() == [KIND_RSS, KIND_X, KIND_YOUTUBE]


def test_registry_returns_the_right_normalizer():
    assert isinstance(normalizer_for(KIND_X), XNormalizer)
    assert isinstance(normalizer_for(KIND_RSS), RssNormalizer)
    assert isinstance(normalizer_for(KIND_YOUTUBE), YoutubeNormalizer)


def test_registry_returns_none_for_an_unknown_kind():
    assert normalizer_for("telepathy") is None


# --- X --------------------------------------------------------------------
def test_x_tweet_text_becomes_content_not_title():
    item = XNormalizer().normalize_one(tweet())
    assert item.content == "hello world"
    assert item.title is None


def test_x_source_id_is_never_the_provider_name():
    """The same tweet can arrive via nitter or xtf and must keep one identity."""
    assert XNormalizer().normalize_one(tweet(provider="xtf")).source_id == SOURCE_X
    assert XNormalizer().normalize_one(tweet(provider="nitter")).identity_key == "x:42"


def test_x_publisher_is_the_author_handle():
    assert XNormalizer().normalize_one(tweet()).publisher == "jack"


def test_x_publisher_falls_back_to_the_tracked_timeline():
    item = XNormalizer().normalize_one(tweet(author_username=None, account="timeline"))
    assert item.publisher == "timeline"


def test_x_publisher_is_none_when_nothing_is_known():
    item = XNormalizer().normalize_one(tweet(author_username=None, account=""))
    assert item.publisher is None


def test_x_canonical_url_prefers_the_raw_url():
    item = XNormalizer().normalize_one(tweet(url="https://x.com/jack/status/42#m"))
    assert item.canonical_url == "https://x.com/jack/status/42#m"


def test_x_canonical_url_is_rebuilt_when_missing():
    item = XNormalizer().normalize_one(tweet(url=""))
    assert item.canonical_url == "https://x.com/jack/status/42"


def test_x_canonical_url_is_none_without_an_author_or_url():
    item = XNormalizer().normalize_one(tweet(url="", author_username=None, account=""))
    assert item.canonical_url is None


def test_x_published_time_is_carried_through():
    item = XNormalizer().normalize_one(tweet())
    assert item.published_at == "2024-09-11T19:31:00+00:00"
    assert item.published_at_raw == "Sep 11, 2024 · 7:31 PM UTC"
    assert item.published_precision == PRECISION_SECOND


def test_x_relative_time_is_never_promoted_to_an_absolute_date():
    """Nitter renders a fresh tweet as ``3h``; that is not a timestamp."""
    item = XNormalizer().normalize_one(tweet(created_at=None, created_at_raw="3h"))
    assert item.published_at is None
    assert item.published_at_raw == "3h"
    assert item.published_precision == PRECISION_UNKNOWN


def test_x_media_is_carried_over():
    raw = tweet(media=[
        Media(url="https://pbs.twimg.com/a.jpg", type="image", thumbnail=None),
        Media(url="https://video.twimg.com/b.mp4", type="video",
              thumbnail="https://pbs.twimg.com/b.jpg"),
    ])
    item = XNormalizer().normalize_one(raw)
    assert [m.type for m in item.media] == [IMAGE, VIDEO]
    assert item.media[1].thumbnail == "https://pbs.twimg.com/b.jpg"


def test_x_without_media_has_an_empty_list_not_none():
    item = XNormalizer().normalize_one(tweet(media=[]))
    assert item.media == []


def test_x_metadata_keeps_source_specific_facts():
    """Engagement and relationships are real but not shared-contract material."""
    raw = tweet(stats=Stats(likes=5, retweets=2))
    item = XNormalizer().normalize_one(raw)
    assert item.metadata["account"] == "jack"
    assert item.metadata["provider"] == "nitter"
    assert item.metadata["route"] == "html"
    assert item.metadata["stats"]["likes"] == 5
    assert item.metadata["stats"]["retweets"] == 2
    # Provenance must not become a contract field.
    assert not hasattr(item, "provider")


def test_x_normalizer_never_emits_telegram_markup():
    """``[from @user](url)`` is a renderer's business, not the model's."""
    item = XNormalizer().normalize_one(tweet())
    for value in item.to_dict().values():
        if isinstance(value, str):
            assert "[from @" not in value
    assert not hasattr(item, "telegram_text")
    assert not hasattr(item, "markdown")


def test_x_normalizer_discards_a_foreign_record():
    assert XNormalizer().normalize_one(feed_item()) is None


def test_x_normalizer_drops_a_tweet_with_no_text():
    """A tweet with no text and no media carries no content."""
    assert XNormalizer().normalize([tweet(text="")]) == []


def test_x_normalizer_batch_keeps_only_valid_items():
    items = XNormalizer().normalize([tweet(tweet_id="1"), tweet(tweet_id="", text="")])
    assert [item.item_id for item in items] == ["1"]


def test_x_normalizer_stamps_fetched_at_when_the_raw_record_lacks_one():
    item = XNormalizer().normalize_one(tweet(fetched_at=""))
    assert item.fetched_at


# --- RSS ------------------------------------------------------------------
def test_rss_guid_is_used_when_it_is_a_real_identifier():
    raw = feed_item(item_id_raw="urn:uuid:1234", link="https://example.com/a")
    item = RssNormalizer().normalize_one(raw)
    assert item.item_id == "urn:uuid:1234"
    assert item.metadata["identity_basis"] == "guid"


def test_rss_guid_that_merely_repeats_the_link_is_not_an_identifier():
    """The VNovel feed does exactly this for 24 of 24 items."""
    raw = feed_item(item_id_raw="https://example.com/a", link="https://example.com/a")
    item = RssNormalizer().normalize_one(raw)
    assert item.item_id == "a"
    assert item.metadata["identity_basis"] == "url"


def test_rss_falls_back_to_the_url_when_there_is_no_guid():
    item = RssNormalizer().normalize_one(feed_item(link="https://example.com/news/2026/84674"))
    assert item.item_id == "84674"
    assert item.metadata["identity_basis"] == "url"


def test_rss_identity_is_never_derived_from_title_or_time():
    """Both change without the item changing."""
    first = RssNormalizer().normalize_one(feed_item(link="https://e.example/a", title="One"))
    second = RssNormalizer().normalize_one(
        feed_item(link="https://e.example/a", title="Totally rewritten",
                  published_raw="Tue, 29 Sep 2026 00:00:00 +0000")
    )
    assert first.item_id == second.item_id


def test_rss_source_id_comes_from_the_config_id():
    item = RssNormalizer().normalize_one(feed_item(source_id="gamebiz"))
    assert item.source_id == "gamebiz"
    assert item.identity_key.startswith("gamebiz:")


def test_rss_content_prefers_content_encoded_over_description():
    raw = feed_item(summary="short lead", content="<p>the full article</p>")
    assert RssNormalizer().normalize_one(raw).content == "<p>the full article</p>"


def test_rss_content_falls_back_to_the_description():
    raw = feed_item(summary="short lead", content="")
    assert RssNormalizer().normalize_one(raw).content == "short lead"


def test_rss_publisher_is_the_feed_title():
    item = RssNormalizer().normalize_one(feed_item(feed_title="Automaton"))
    assert item.publisher == "Automaton"


def test_rss_publisher_falls_back_to_the_item_author():
    item = RssNormalizer().normalize_one(feed_item(feed_title="", author="Someone"))
    assert item.publisher == "Someone"


def test_rss_publisher_does_not_parse_a_bracketed_title_prefix():
    """``[Gamer] Title`` is one project's renderer convention, not a standard."""
    item = RssNormalizer().normalize_one(
        feed_item(title="[Gamer] Something happened", feed_title="")
    )
    assert item.publisher is None
    assert item.title == "[Gamer] Something happened"


def test_rss_publisher_is_none_when_nothing_is_known():
    assert RssNormalizer().normalize_one(feed_item(feed_title="", author="")).publisher is None


def test_rss_missing_fields_stay_none_rather_than_being_invented():
    raw = feed_item(title="", summary="", content="", published_raw="",
                    published_at=None, published_precision=PRECISION_UNKNOWN,
                    feed_title="", author="")
    item = RssNormalizer().normalize_one(raw)
    assert item.title is None
    assert item.content is None
    assert item.publisher is None
    assert item.published_at is None
    assert item.published_precision == PRECISION_UNKNOWN
    assert item.published_at_raw == ""
    # And it is rejected downstream, because it carries nothing.
    assert not item.is_valid()


def test_rss_date_only_publication_keeps_day_precision():
    raw = feed_item(published_raw="2026-09-28", published_at="2026-09-28",
                    published_precision=PRECISION_DAY)
    item = RssNormalizer().normalize_one(raw)
    assert item.published_at == "2026-09-28"
    assert item.published_precision == PRECISION_DAY


def test_rss_image_enclosure_becomes_media():
    raw = feed_item(enclosure_url="https://e.example/a.jpg", enclosure_type="image/jpeg")
    item = RssNormalizer().normalize_one(raw)
    assert len(item.media) == 1
    assert item.media[0].type == IMAGE
    assert item.media[0].mime_type == "image/jpeg"


def test_rss_gif_enclosure_is_typed_as_a_gif():
    raw = feed_item(enclosure_url="https://e.example/a.gif", enclosure_type="image/gif")
    assert RssNormalizer().normalize_one(raw).media[0].type == GIF


def test_rss_video_enclosure_becomes_media():
    raw = feed_item(enclosure_url="https://e.example/a.mp4", enclosure_type="video/mp4")
    assert RssNormalizer().normalize_one(raw).media[0].type == VIDEO


def test_rss_media_type_is_guessed_from_the_extension_when_absent():
    raw = feed_item(enclosure_url="https://e.example/a.PNG", enclosure_type="")
    assert RssNormalizer().normalize_one(raw).media[0].type == IMAGE


def test_rss_audio_enclosure_is_kept_as_evidence_not_mislabelled():
    """The media vocabulary has no audio member (DR-7); do not lie about it."""
    raw = feed_item(enclosure_url="https://e.example/a.mp3", enclosure_type="audio/mpeg")
    item = RssNormalizer().normalize_one(raw)
    assert item.media == []
    assert item.metadata["enclosure"]["url"] == "https://e.example/a.mp3"
    assert item.metadata["enclosure"]["type"] == "audio/mpeg"


def test_rss_categories_are_preserved_in_metadata():
    item = RssNormalizer().normalize_one(feed_item(categories=["ゲーム", "ニュース"]))
    assert item.metadata["categories"] == ["ゲーム", "ニュース"]


def test_rss_updated_raw_is_kept_only_when_it_differs():
    same = feed_item(updated_raw="Mon, 28 Sep 2026 11:45:00 +0000")
    assert "updated_raw" not in RssNormalizer().normalize_one(same).metadata

    different = feed_item(updated_raw="Tue, 29 Sep 2026 08:00:00 +0000")
    assert RssNormalizer().normalize_one(different).metadata["updated_raw"] == (
        "Tue, 29 Sep 2026 08:00:00 +0000"
    )


def test_rss_canonical_url_is_the_link():
    item = RssNormalizer().normalize_one(feed_item(link="https://e.example/a?ref=x"))
    assert item.canonical_url == "https://e.example/a?ref=x"


def test_rss_normalizer_discards_a_foreign_record():
    assert RssNormalizer().normalize_one(tweet()) is None


def test_rss_normalizer_batch_filters_invalid_records():
    valid = feed_item(link="https://e.example/a")
    empty = feed_item(title="", summary="", content="", link="https://e.example/b")
    items = RssNormalizer().normalize([valid, empty])
    assert [item.item_id for item in items] == ["a"]


# --- item_id_from_url -----------------------------------------------------
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://e.example/article/84674", "84674"),
        ("https://e.example/article/84674/", "84674"),
        ("https://e.example/news/2026/09/28/story.html", "story"),
        ("https://e.example/a.php?id=3", "a"),
        ("https://e.example/", "https://e.example/"),
        ("", ""),
    ],
)
def test_url_identity_reduction(url, expected):
    assert item_id_from_url(url) == expected


def test_url_identity_ignores_the_fragment_and_query():
    assert item_id_from_url("https://e.example/a#section") == item_id_from_url(
        "https://e.example/a?utm_source=x"
    )


def test_host_only_urls_do_not_collide_across_hosts():
    """``https://s.example/`` must not reduce to ``"s"``."""
    assert item_id_from_url("https://s.example/") != item_id_from_url("https://t.example/")


# --- YouTube --------------------------------------------------------------
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
        url="",
        duration="PT3M33S",
        view_count=1000,
        fetched_at=FETCHED,
        raw={},
    )
    data.update(overrides)
    return RawYouTubeItem(**data)


def test_youtube_channel_becomes_publisher():
    """A channel is not an author and not an account — which is why the field
    is named ``publisher``."""
    item = YoutubeNormalizer().normalize_one(video())
    assert item.publisher == "Some Channel"
    # Phase 4: the namespace is per channel, not one shared "youtube", so two
    # channels are isolated by construction (see normalizers/youtube.py).
    assert item.source_id == f"{SOURCE_YOUTUBE}:UC123"


def test_youtube_video_id_is_the_identity():
    item = YoutubeNormalizer().normalize_one(video())
    assert item.item_id == "dQw4w9WgXcQ"
    assert item.identity_key == f"{SOURCE_YOUTUBE}:UC123:dQw4w9WgXcQ"


def test_youtube_namespace_falls_back_when_the_channel_id_is_missing():
    """A feed that lost its channel id must not produce ``"youtube:"``."""
    item = YoutubeNormalizer().normalize_one(video(channel_id=""))
    assert item.source_id == SOURCE_YOUTUBE
    assert item.identity_key == f"{SOURCE_YOUTUBE}:dQw4w9WgXcQ"


def test_youtube_watch_url_is_built_when_missing():
    item = YoutubeNormalizer().normalize_one(video())
    assert item.canonical_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_youtube_thumbnail_becomes_media():
    item = YoutubeNormalizer().normalize_one(video())
    assert len(item.media) == 1
    assert item.media[0].type == IMAGE


def test_youtube_duration_and_views_stay_in_metadata():
    item = YoutubeNormalizer().normalize_one(video())
    assert item.metadata["duration"] == "PT3M33S"
    assert item.metadata["view_count"] == 1000
    assert item.metadata["channel_id"] == "UC123"


def test_youtube_without_a_published_time_has_unknown_precision():
    item = YoutubeNormalizer().normalize_one(
        video(published_at=None, published_raw="", published_precision=PRECISION_SECOND)
    )
    assert item.published_precision == PRECISION_UNKNOWN


def test_youtube_normalizer_discards_a_foreign_record():
    assert YoutubeNormalizer().normalize_one(tweet()) is None


def test_every_normalizer_produces_the_same_type():
    """Three shapes, one contract. This is the whole point of the freeze."""
    outputs = [
        XNormalizer().normalize_one(tweet()),
        RssNormalizer().normalize_one(feed_item()),
        YoutubeNormalizer().normalize_one(video()),
    ]
    assert all(isinstance(item, NormalizedItem) for item in outputs)
    assert all(item.is_valid() for item in outputs)
    # And they are all distinguishable by source, never by provider.
    assert [item.source_id for item in outputs] == ["x", "vnovel", f"{SOURCE_YOUTUBE}:UC123"]


def test_media_item_round_trips_through_a_dict():
    media = MediaItem(url="https://e.example/a.jpg", type=IMAGE, width=100, height=50)
    assert MediaItem.from_dict(media.to_dict()) == media
