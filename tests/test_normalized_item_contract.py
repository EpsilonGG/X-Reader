"""Executable validation of the Architecture Freeze v3 contract.

This is *not* a test of production behaviour — no Normalizer exists yet and
``domain/models/item.py`` is not wired into the pipeline. It is a **design
test**: it proves the frozen contract can absorb the real data of all three
sources without inventing fields and without silently dropping information.

The three sources are deliberately unequal in strength of evidence:

* **X** — real markup (``fixtures/nitter_timeline.html``) through the real
  Phase 1 parser, so the raw side is genuine.
* **Web / RSS** — real committed output of ``VisualNovel-Interview-RSS-main``
  (``fixtures/vnovel_rss_sample.xml``, 24 items, copied verbatim from that
  project's ``rss.xml``), so both the shapes and the *gaps* are genuine.
* **YouTube** — no real sample exists in this workspace, so it is validated
  against the minimum YouTube exposes. It is marked as such: this test proves
  the contract is *sufficient*, not that it has met real YouTube data.

The mapping functions below are reference mappings. They are the written-down
answer to "how does source X become a NormalizedItem", and Phase 2's
Normalizers are expected to match them.
"""
from __future__ import annotations

import json
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest

from domain.models.item import GIF
from domain.models.item import IMAGE
from domain.models.item import PRECISION_SECOND
from domain.models.item import PRECISION_UNKNOWN
from domain.models.item import RESERVED_SOURCE_IDS
from domain.models.item import SOURCE_X
from domain.models.item import SOURCE_YOUTUBE
from domain.models.item import VIDEO
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from domain.models.item import build_identity_key
from domain.models.tweet import RawTweet
from parsers.nitter_html import NitterHtmlParser

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OBSERVED_AT = "2026-09-28T12:00:00+00:00"


# =============================================================================
# Reference mappings (freeze v3, sections 13 / 14 / 15)
# =============================================================================
def x_item(tweet: RawTweet) -> NormalizedItem:
    """X -> NormalizedItem.

    Note what is *not* promoted to a shared field: engagement counters, the
    retweet/reply/quote relationships and the tracked timeline. They are real
    but only X has them, so they go to ``metadata`` (freeze section 10).
    """
    return NormalizedItem(
        source_id=SOURCE_X,
        item_id=tweet.tweet_id,
        # A tweet has no title. This is the single most important proof that
        # ``title`` must be optional.
        title=None,
        content=tweet.text,
        publisher=tweet.author_username or None,
        canonical_url=tweet.url or None,
        published_at=tweet.created_at,
        published_at_raw=tweet.created_at_raw,
        published_precision=PRECISION_SECOND if tweet.created_at else PRECISION_UNKNOWN,
        fetched_at=tweet.fetched_at or OBSERVED_AT,
        media=[
            MediaItem(url=m.url, type=m.type, thumbnail=m.thumbnail) for m in tweet.media
        ],
        metadata={
            "account": tweet.account,
            "author_name": tweet.author_name,
            "is_retweet": tweet.is_retweet,
            "retweeted_by": tweet.retweeted_by,
            "is_reply": tweet.is_reply,
            "reply_to": tweet.reply_to,
            "is_quote": tweet.is_quote,
            "quoted_tweet_id": tweet.quoted_tweet_id,
            "stats": tweet.stats.to_dict(),
            "provider": tweet.provider,
            "route": tweet.route,
        },
    )


def _slug(site: str) -> str:
    return site.strip().lower().replace("*", "").replace(" ", "")


def _item_id_from_url(url: str) -> str:
    """Fallback identity when the feed carries no independent guid.

    Degradation, explicitly accepted: if the site later rewrites its URLs, the
    item looks new. Recorded in ``metadata['identity_basis']`` so the weakness
    is visible in stored data instead of hidden.
    """
    tail = url.rstrip("/").split("/")[-1]
    return tail.rsplit(".", 1)[0] or url


def web_item(entry, *, fetched_at: str = OBSERVED_AT) -> NormalizedItem:
    """One RSS ``<item>`` of the VNovel family -> NormalizedItem.

    The VNovel output prefixes the site into the title (``[Gamer] ...``) because
    its own RSS renderer has nowhere else to put it. NormalizedItem *does* have
    somewhere else, so the prefix is lifted back out into ``publisher`` -- that
    is the point of the shared model.
    """
    raw_title = (entry.findtext("title") or "").strip()
    link = (entry.findtext("link") or "").strip()
    guid = (entry.findtext("guid") or "").strip()
    raw_date = (entry.findtext("pubDate") or "").strip()

    site = ""
    title = raw_title
    if raw_title.startswith("[") and "]" in raw_title:
        site, _, rest = raw_title[1:].partition("]")
        site, title = site.strip(), rest.strip()

    item_id = guid or _item_id_from_url(link)
    basis = "guid" if guid else "url"

    published_at: str | None = None
    precision = PRECISION_UNKNOWN
    if raw_date:
        try:
            published_at = parsedate_to_datetime(raw_date).astimezone(
                timezone.utc
            ).isoformat()
            # The feed prints seconds, so that is the best it can honestly claim.
            # Whether those seconds are *real* is unknowable from the feed alone
            # -- see test_web_date_only_upstream_is_indistinguishable.
            precision = PRECISION_SECOND
        except (TypeError, ValueError):
            published_at, precision = None, PRECISION_UNKNOWN

    media: list[MediaItem] = []
    enclosure = entry.find("enclosure")
    if enclosure is not None and enclosure.get("url"):
        media.append(
            MediaItem(
                url=enclosure.get("url"),
                type=IMAGE,
                mime_type=enclosure.get("type") or None,
            )
        )

    return NormalizedItem(
        source_id=_slug(site),
        item_id=item_id,
        title=title or None,
        # The VNovel ``description`` is a lead/summary, not the full body. It
        # maps to ``content`` because that is the best available body text;
        # nobody claims it is the article.
        content=(entry.findtext("description") or "").strip() or None,
        publisher=site or None,
        canonical_url=link or None,
        published_at=published_at,
        published_at_raw=raw_date,
        published_precision=precision,
        fetched_at=fetched_at,
        media=media,
        metadata={"identity_basis": basis, "category": "interview"},
    )


def youtube_item(payload: dict) -> NormalizedItem:
    """YouTube -> NormalizedItem (interface only; no real sample available)."""
    return NormalizedItem(
        source_id=SOURCE_YOUTUBE,
        item_id=payload["video_id"],
        title=payload["title"],
        content=payload.get("description"),
        publisher=payload["channel"],
        canonical_url=f"https://www.youtube.com/watch?v={payload['video_id']}",
        published_at=payload.get("published_at"),
        published_at_raw=payload.get("published_at", ""),
        published_precision=PRECISION_SECOND if payload.get("published_at") else PRECISION_UNKNOWN,
        fetched_at=OBSERVED_AT,
        media=[
            MediaItem(
                url=f"https://i.ytimg.com/vi/{payload['video_id']}/maxresdefault.jpg",
                type=IMAGE,
            )
        ],
        metadata={
            "channel_id": payload.get("channel_id"),
            "duration": payload.get("duration"),
            "view_count": payload.get("view_count"),
        },
    )


# =============================================================================
# Fixtures / helpers
# =============================================================================
def _vnovel_entries():
    tree = ElementTree.parse(FIXTURES / "vnovel_rss_sample.xml")
    return tree.getroot().findall("./channel/item")


@pytest.fixture
def x_items(account, timeline_response) -> list[NormalizedItem]:
    """Real Nitter markup through the real Phase 1 parser."""
    tweets = NitterHtmlParser().parse(timeline_response, account)
    for tweet in tweets:
        tweet.fetched_at = timeline_response.fetched_at.isoformat()
    return [x_item(t) for t in tweets]


@pytest.fixture
def web_items() -> list[NormalizedItem]:
    return [web_item(e) for e in _vnovel_entries()]


YOUTUBE_SAMPLE = {
    "video_id": "dQw4w9WgXcQ",
    "channel": "SomeChannel",
    "channel_id": "UC0000000000000000000000",
    "title": "A video title",
    "description": "A description.",
    "published_at": "2026-09-01T10:00:00+00:00",
    "duration": "PT3M33S",
    "view_count": 12345,
}


# =============================================================================
# X -> NormalizedItem
# =============================================================================
def test_x_real_fixture_maps_cleanly(x_items):
    assert x_items, "fixture produced no tweets"
    for item in x_items:
        assert item.is_valid(), (item.identity_key, item.missing_required())
        assert item.source_id == SOURCE_X
        assert item.item_id.isdigit()
        assert item.canonical_url.startswith("https://x.com/")


def test_x_tweet_has_no_title_but_has_content(x_items):
    """A tweet is content-only. This is why ``title`` cannot be required."""
    assert all(i.title is None for i in x_items)
    assert all(i.content for i in x_items)


def test_x_publisher_is_the_author_handle(x_items):
    assert all(i.publisher for i in x_items)
    assert all(not i.publisher.startswith("@") for i in x_items)


def test_x_media_survives_including_video_poster(x_items):
    """The poster frame is real data Phase 1 already captures; it must not be
    dropped just because the minimal MediaItem shape did not mention it."""
    media = [m for i in x_items for m in i.media]
    assert media, "fixture should contain attachments"
    kinds = {m.type for m in media}
    assert kinds <= {IMAGE, VIDEO, GIF}
    posters = [m for m in media if m.type == VIDEO and m.thumbnail]
    assert posters, "a video must keep its poster frame"


def test_x_published_time_keeps_source_rendering(x_items):
    """Most tweets carry an absolute date; the raw rendering is always kept."""
    dated = [i for i in x_items if i.published_at]
    assert dated, "fixture tweets mostly carry a date"
    for item in dated:
        assert item.published_at_raw
        assert item.published_precision == PRECISION_SECOND


def test_x_relative_time_is_never_promoted_to_an_absolute_date(x_items):
    """Real evidence: Nitter renders a fresh tweet as ``3h``.

    Turning that into a timestamp would be a fabrication, so the contract keeps
    ``published_at`` empty and preserves the source's own text. This is the
    second, independent reason ``published_at`` must be optional.
    """
    undated = [i for i in x_items if i.published_at is None]
    assert undated, "the fixture contains a relative-time tweet"
    for item in undated:
        assert item.published_at_raw, "the source rendering must not be thrown away"
        assert item.published_precision == PRECISION_UNKNOWN
        assert item.is_valid(), "a tweet with no date is still a valid item"


def test_x_engagement_and_relations_are_metadata(x_items):
    """Only X has these, so they must not become shared fields."""
    item = x_items[0]
    assert "stats" in item.metadata
    assert {"is_retweet", "is_reply", "is_quote"} <= set(item.metadata)
    for name in ("stats", "is_retweet", "is_reply", "is_quote"):
        assert not hasattr(NormalizedItem, name)


# =============================================================================
# VisualNovel-Interview-RSS -> NormalizedItem
# =============================================================================
def test_web_real_rss_maps_cleanly(web_items):
    assert len(web_items) == 24, "real sample has 24 items"
    for item in web_items:
        assert item.is_valid(), (item.identity_key, item.missing_required())
        assert item.source_id not in RESERVED_SOURCE_IDS
        assert item.title, "every VNovel item has a title"
        assert item.canonical_url.startswith("http")


def test_web_site_prefix_is_lifted_into_publisher(web_items):
    """``[Gamer] Title`` must become publisher=Gamer, title=Title."""
    assert all(i.publisher for i in web_items)
    assert all(not i.title.startswith("[") for i in web_items)
    sites = {i.publisher for i in web_items}
    assert len(sites) >= 5, f"expected several distinct sites, got {sites}"


def test_web_identity_is_url_shaped_when_guid_is_the_link(web_items):
    """The real feed sets ``guid == link``, i.e. the only available identity is
    the URL itself. Recorded as a fact so the weakness is not mistaken for a
    proper source-native id."""
    assert all(i.metadata["identity_basis"] == "guid" for i in web_items)
    assert all(i.item_id == i.canonical_url for i in web_items)


def test_web_missing_pubdate_is_representable(web_items):
    """Half the real sample has no date at all. The contract must not invent one."""
    undated = [i for i in web_items if i.published_at is None]
    assert undated, "real sample contains items without a date"
    for item in undated:
        assert item.published_precision == PRECISION_UNKNOWN
        assert item.published_at_raw == ""
        assert item.is_valid(), "a dateless item is still a valid item"


def test_web_date_only_upstream_is_indistinguishable(web_items):
    """Evidence that ``published_precision`` cannot be *inferred* downstream.

    The upstream VNovel pipeline promotes a date-only scrape (``(2026/9/27)``)
    to midnight UTC, so the feed now claims second precision it never had.
    A Normalizer reading only the feed cannot tell the difference -- which is
    why precision must travel with the item from the source that knows it.
    """
    midnight = [
        i
        for i in web_items
        if i.published_at and i.published_at[11:19] == "00:00:00"
    ]
    assert midnight, "the real sample contains a promoted date-only value"
    assert all(i.published_precision == PRECISION_SECOND for i in midnight)


def test_web_media_is_optional(web_items):
    assert any(i.media for i in web_items)
    assert any(not i.media for i in web_items), "3 of 24 real items have no image"


# =============================================================================
# YouTube -> NormalizedItem (interface only)
# =============================================================================
def test_youtube_sample_maps_cleanly():
    item = youtube_item(YOUTUBE_SAMPLE)
    assert item.is_valid()
    assert item.source_id == SOURCE_YOUTUBE
    assert item.item_id == "dQw4w9WgXcQ"
    assert item.title and item.content
    assert item.publisher == "SomeChannel"
    assert item.published_precision == PRECISION_SECOND
    assert item.media[0].type == IMAGE


def test_youtube_needs_no_new_field():
    """Nothing YouTube requires is missing from the frozen contract."""
    item = youtube_item(YOUTUBE_SAMPLE)
    assert set(item.metadata) == {"channel_id", "duration", "view_count"}
    for name in ("channel", "video_id", "duration", "view_count"):
        assert not hasattr(NormalizedItem, name)


# =============================================================================
# Identity / dedup
# =============================================================================
def test_identity_keys_are_unique_across_all_three_sources(x_items, web_items):
    keys = [i.identity_key for i in x_items + web_items + [youtube_item(YOUTUBE_SAMPLE)]]
    assert len(keys) == len(set(keys)) + _real_duplicate_count(web_items)


def _real_duplicate_count(web_items) -> int:
    """How many duplicate identity keys the *real* VNovel feed itself contains.

    Measured, not assumed: see ``test_web_real_feed_contains_a_duplicate_item``.
    """
    keys = [i.identity_key for i in web_items]
    return len(keys) - len(set(keys))


def test_web_real_feed_contains_a_duplicate_item(web_items):
    """The real feed ships the same article twice.

    Evidence, from ``VisualNovel-Interview-RSS-main``: its ``main.py`` dedups
    only against ``history.json`` and never within the batch, and its
    ``utils/dedup.remove_duplicates`` helper is defined but never called. The
    committed ``rss.xml`` therefore contains
    ``https://dengekionline.com/article/202609/84674`` twice, and its
    ``history.json`` holds 3277 links of which only 3184 are distinct.

    Consequence for the freeze: dedup must be applied to the **batch**, by
    ``identity_key``, inside the shared layer -- not delegated to each source.
    """
    keys = [i.identity_key for i in web_items]
    duplicates = {k for k in keys if keys.count(k) > 1}
    assert duplicates, "expected the real sample to contain a duplicate"
    assert any("dengekionline.com" in k for k in duplicates)

    # And the shared layer's answer is available immediately: identity_key
    # collapses them without any source-specific knowledge.
    deduped = list({i.identity_key: i for i in web_items}.values())
    assert len(deduped) == len(set(keys))


def test_same_numeric_id_in_two_sources_does_not_collide():
    """Two sites reusing the same article id must stay distinct."""
    a = NormalizedItem(source_id="gamer", item_id="123", content="a", fetched_at=OBSERVED_AT)
    b = NormalizedItem(source_id="gamebiz", item_id="123", content="b", fetched_at=OBSERVED_AT)
    assert a.identity_key != b.identity_key


def test_identity_key_is_derived_not_stored():
    """A stored copy could drift; a property cannot."""
    item = NormalizedItem(source_id="x", item_id="42", content="hi", fetched_at=OBSERVED_AT)
    assert item.identity_key == build_identity_key("x", "42")
    item.item_id = "43"
    assert item.identity_key == "x:43"


def test_identity_survives_a_url_change():
    """Identity is not the URL, so a permalink rewrite is not a new item."""
    before = NormalizedItem(
        source_id="x", item_id="42", content="hi", fetched_at=OBSERVED_AT,
        canonical_url="https://x.com/a/status/42",
    )
    after = NormalizedItem(
        source_id="x", item_id="42", content="hi", fetched_at=OBSERVED_AT,
        canonical_url="https://x.com/b/status/42",
    )
    assert before.identity_key == after.identity_key


def test_url_derived_identity_is_the_documented_degradation():
    """When identity comes from the URL, rewriting the URL forks the item.

    Asserted rather than hidden: this is the accepted cost, and
    ``metadata['identity_basis']`` makes it visible in stored data.
    """
    with_guid = web_item(
        _fake_entry(
            link="https://s.example/news/1.html",
            guid="https://s.example/news/1.html",
        )
    )
    assert with_guid.metadata["identity_basis"] == "guid"

    no_guid = web_item(_fake_entry(link="https://s.example/news/1.html", guid=""))
    assert no_guid.metadata["identity_basis"] == "url"
    assert no_guid.item_id == "1"

    moved = web_item(_fake_entry(link="https://s.example/news/2.html", guid=""))
    assert moved.identity_key != no_guid.identity_key


def _fake_entry(*, link: str, guid: str):
    from xml.etree.ElementTree import Element
    from xml.etree.ElementTree import SubElement

    entry = Element("item")
    SubElement(entry, "title").text = "[S] t"
    SubElement(entry, "link").text = link
    if guid:
        SubElement(entry, "guid").text = guid
    return entry


# =============================================================================
# Validation rules
# =============================================================================
def test_missing_required_fields_are_reported():
    item = NormalizedItem(source_id="", item_id="", content="x")
    assert item.missing_required() == ["source_id", "item_id", "fetched_at"]
    assert not item.is_valid()


def test_contentless_item_is_rejected():
    item = NormalizedItem(source_id="x", item_id="1", fetched_at=OBSERVED_AT)
    assert item.missing_required() == []
    assert not item.is_meaningful()
    assert not item.is_valid()


def test_item_without_url_is_still_valid():
    """Identity is source_id:item_id, so a missing URL is not disqualifying."""
    item = NormalizedItem(source_id="x", item_id="1", content="hi", fetched_at=OBSERVED_AT)
    assert item.canonical_url is None
    assert item.is_valid()


def test_serialisation_is_json_safe(x_items, web_items):
    for item in x_items + web_items:
        assert json.loads(json.dumps(item.to_dict(), ensure_ascii=False))
        assert item.to_dict()["identity_key"] == item.identity_key


def test_media_dict_omits_absent_optionals():
    assert MediaItem(url="u").to_dict() == {"url": "u", "type": IMAGE}
    assert MediaItem(url="u", type=VIDEO, thumbnail="p").to_dict() == {
        "url": "u",
        "type": VIDEO,
        "thumbnail": "p",
    }


def test_contract_does_not_reintroduce_author_as_a_field():
    """The VNovel README advertises an author; its real model has none."""
    assert not hasattr(NormalizedItem, "author")
    assert hasattr(NormalizedItem, "publisher")
