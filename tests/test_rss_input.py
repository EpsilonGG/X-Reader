"""Generic RSS input: provider, parser, normalizer, and the end-to-end chain.

Section 19 of the Phase 2 task makes one acceptance requirement explicit: the
chain must be exercised against the **real** RSS output of
``VisualNovel-Interview-RSS-main``, without modifying that project. Its committed
``rss.xml`` is copied verbatim to ``tests/fixtures/vnovel_rss_sample.xml`` and
used below.

What that real feed teaches, and what these tests therefore assert:

* ``guid == link`` for all 24 items — a ``guid`` that repeats the link is not an
  identifier, so identity falls back to the URL;
* only 12 of 24 items carry a date — ``published_at`` must be optional;
* 21 of 24 carry an ``enclosure``, all images — media must survive;
* two of the 24 items share a URL — the duplicate must collapse to one identity
  rather than being stored twice.

None of this is X-Reader's business to *fix*. It is the input contract's job to
absorb it, which is exactly what is verified here.
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest

from config.schema import Config
from config.schema import RssSourceConfig
from domain.errors import ParsingError
from domain.errors import ProviderError
from domain.models.account import Account
from domain.models.item import PRECISION_DAY
from domain.models.item import PRECISION_HOUR
from domain.models.item import PRECISION_MINUTE
from domain.models.item import PRECISION_SECOND
from domain.models.item import PRECISION_UNKNOWN
from infrastructure.http.response import RawResponse
from normalizers.rss import RssNormalizer
from parsers.rss import RssParser
from parsers.rss import parse_feed_time
from providers.factory import build_rss_provider
from providers.rss import ROUTE_FEED
from providers.rss import RssProvider
from storage.jsonl import JsonlStorage
from tests.conftest import fixture_text

VNOVEL_RSS = fixture_text("vnovel_rss_sample.xml")
MALFORMED = fixture_text("malformed_feed.xml")

FETCHED = datetime(2024, 9, 11, 19, 31, tzinfo=timezone.utc)


def feed_response(text: str, url: str = "https://v.example/rss.xml") -> RawResponse:
    return RawResponse(
        url=url,
        status_code=200,
        content_type="application/rss+xml",
        text=text,
        fetched_at=FETCHED,
    )


RSS_2_0 = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <link>https://e.example</link>
    <description>An example</description>
    <item>
      <title>First article</title>
      <link>https://e.example/news/1234</link>
      <guid>https://e.example/news/1234</guid>
      <description>Short lead</description>
      <pubDate>Mon, 28 Sep 2026 11:45:00 +0000</pubDate>
      <enclosure url="https://e.example/a.jpg" type="image/jpeg" length="1234" />
      <category>games</category>
    </item>
    <item>
      <title>Second article</title>
      <link>https://e.example/news/5678</link>
      <guid isPermaLink="false">urn:uuid:5678</guid>
      <description>Another lead</description>
      <pubDate>Sun, 27 Sep 2026 09:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Example</title>
  <link href="https://a.example/" rel="alternate" />
  <updated>2026-09-28T11:45:00Z</updated>
  <entry>
    <title>Atom entry</title>
    <link rel="alternate" href="https://a.example/entry/1" />
    <link rel="enclosure" href="https://a.example/1.jpg" type="image/jpeg" />
    <id>tag:a.example,2026:1</id>
    <published>2026-09-28T11:45:00Z</published>
    <updated>2026-09-29T08:00:00Z</updated>
    <summary>Atom summary</summary>
    <author><name>Atom Author</name></author>
  </entry>
</feed>
"""

CONTENT_ENCODED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Encoded</title>
    <item>
      <title>Full body</title>
      <link>https://e.example/full</link>
      <description>teaser only</description>
      <content:encoded><![CDATA[<p>The whole article.</p>]]></content:encoded>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def source() -> RssSourceConfig:
    return RssSourceConfig(id="vnovel", url="https://v.example/rss.xml")


@pytest.fixture
def unit() -> Account:
    """The tracked-unit slot carrying an RSS source id."""
    return Account(username="vnovel")


# --- provider -------------------------------------------------------------
def test_provider_advertises_exactly_one_route(source):
    assert RssProvider(sources=[source], client=None).routes() == (ROUTE_FEED,)


def test_provider_is_available_with_one_usable_source(source):
    assert RssProvider(sources=[source], client=None).available() is True


def test_provider_is_unavailable_without_sources():
    assert RssProvider(sources=[], client=None).available() is False


def test_provider_is_unavailable_when_every_source_is_disabled():
    disabled = RssSourceConfig(id="vnovel", url="https://v.example/rss.xml", enabled=False)
    assert RssProvider(sources=[disabled], client=None).available() is False


def test_provider_fetches_the_configured_url(source, unit):
    class RecordingClient:
        def __init__(self):
            self.urls: list[str] = []

        def get(self, url):
            self.urls.append(url)
            return feed_response(VNOVEL_RSS, url=url)

    client = RecordingClient()
    response = RssProvider(sources=[source], client=client).fetch(unit, ROUTE_FEED)

    assert client.urls == ["https://v.example/rss.xml"]
    assert response.text == VNOVEL_RSS


def test_provider_rejects_an_unknown_route(source, unit):
    with pytest.raises(ProviderError):
        RssProvider(sources=[source], client=None).fetch(unit, "telepathy")


def test_provider_rejects_an_unknown_source_id(source):
    with pytest.raises(ProviderError):
        RssProvider(sources=[source], client=None).fetch(Account(username="ghost"), ROUTE_FEED)


def test_provider_rejects_a_source_with_no_url():
    broken = RssSourceConfig(id="vnovel", url="https://v.example/rss.xml")
    provider = RssProvider(sources=[broken], client=None)
    provider._by_id["vnovel"] = RssSourceConfig.model_construct(id="vnovel", url="", enabled=True)
    with pytest.raises(ProviderError):
        provider.fetch(Account(username="vnovel"), ROUTE_FEED)


def test_provider_does_not_parse_or_store(source, unit):
    """A provider that knows about XML would break the frozen boundary."""
    provider = RssProvider(sources=[source], client=None)
    assert not hasattr(provider, "parse")
    assert not hasattr(provider, "save")
    assert not hasattr(provider, "normalize")


def rss_config(**overrides) -> Config:
    """A valid X + RSS config.

    ``rss_sources`` is a *separate* config space from ``accounts`` — nothing
    here pretends a feed is an X account. For the RSS-only form (no X input at
    all) see ``test_an_rss_only_config_is_accepted`` below.
    """
    data = {
        "provider": {"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}},
        "rss_sources": [{"id": "vnovel", "url": "https://v.example/rss.xml"}],
    }
    data.update(overrides)
    return Config(**data)


def test_factory_returns_a_provider_when_a_source_is_configured():
    provider = build_rss_provider(rss_config(), client=None)
    assert isinstance(provider, RssProvider)
    assert [source.id for source in provider.sources()] == ["vnovel"]


def test_factory_returns_none_when_nothing_is_configured():
    assert build_rss_provider(Config(
        provider={"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}}
    ), client=None) is None


def test_factory_returns_none_when_every_source_is_disabled():
    config = rss_config(rss_sources=[
        {"id": "vnovel", "url": "https://v.example/rss.xml", "enabled": False}
    ])
    assert build_rss_provider(config, client=None) is None


def test_an_rss_only_config_is_accepted():
    """RSS-only is a first-class input mode, not a workaround.

    ``provider.order: []`` disables X input entirely; the only requirement is
    that *something* is enabled. Before Phase 2's decoupling step this raised,
    because ``provider.order`` was not allowed to be empty — a Phase 1
    assumption that X was the only possible input.

    Note that ``build_rss_provider`` was never gated on ``provider.order``: it
    is deliberately independent, so a feed is reachable whether or not X is.
    """
    config = Config(**{
        "provider": {"order": []},
        "rss_sources": [{"id": "vnovel", "url": "https://v.example/rss.xml"}],
    })
    assert config.x_input_enabled is False
    assert [source.id for source in config.enabled_rss_sources] == ["vnovel"]
    # No Nitter endpoint list was required.
    assert config.provider.nitter.endpoints == []
    assert build_rss_provider(config, client=None) is not None


def test_an_empty_config_is_still_rejected():
    """'No X' is fine; 'nothing at all' is not."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Config(**{"provider": {"order": []}})  # no rss_sources either

    with pytest.raises(ValidationError):
        Config(**{
            "provider": {"order": []},
            "rss_sources": [{"id": "vnovel", "url": "https://v.example/rss.xml",
                             "enabled": False}],
        })


# --- parser: document shapes ----------------------------------------------
def test_parser_reads_rss_2_0(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert [item.title for item in items] == ["First article", "Second article"]


def test_parser_attributes_entries_to_the_tracked_unit(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert all(item.source_id == "vnovel" for item in items)


def test_parser_carries_the_feed_title(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert items[0].feed_title == "Example Feed"


def test_parser_reads_atom(unit):
    items = RssParser().parse(feed_response(ATOM, url="https://a.example/feed"), unit)
    assert len(items) == 1
    entry = items[0]
    assert entry.title == "Atom entry"
    assert entry.link == "https://a.example/entry/1"
    assert entry.item_id_raw == "tag:a.example,2026:1"
    assert entry.summary == "Atom summary"
    assert entry.author == "Atom Author"
    assert entry.enclosure_url == "https://a.example/1.jpg"


def test_parser_prefers_alternate_over_enclosure_for_the_link(unit):
    """An Atom feed's enclosure link must not become the canonical URL."""
    items = RssParser().parse(feed_response(ATOM, url="https://a.example/feed"), unit)
    assert items[0].link == "https://a.example/entry/1"


def test_parser_reads_content_encoded(unit):
    items = RssParser().parse(feed_response(CONTENT_ENCODED), unit)
    assert items[0].content == "<p>The whole article.</p>"
    assert items[0].summary == "teaser only"


def test_parser_reads_a_guid(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert items[0].item_id_raw == "https://e.example/news/1234"
    assert items[1].item_id_raw == "urn:uuid:5678"


def test_parser_reads_an_enclosure(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert items[0].enclosure_url == "https://e.example/a.jpg"
    assert items[0].enclosure_type == "image/jpeg"
    assert items[0].enclosure_length == "1234"


def test_parser_reads_categories(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert items[0].categories == ["games"]


def test_parser_reads_pub_date(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert items[0].published_at == "2026-09-28T11:45:00+00:00"
    assert items[0].published_precision == PRECISION_SECOND
    assert items[0].published_raw == "Mon, 28 Sep 2026 11:45:00 +0000"


def test_parser_keeps_the_raw_extraction_for_re_processing(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert items[0].raw["title"] == "First article"
    assert items[0].raw["guid"] == "https://e.example/news/1234"


def test_parser_stamps_fetched_at_from_the_response(unit):
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert items[0].fetched_at == FETCHED.isoformat()


# --- parser: missing fields ------------------------------------------------
def test_parser_tolerates_a_missing_guid(unit):
    feed = RSS_2_0.replace(
        "<guid>https://e.example/news/1234</guid>", ""
    )
    items = RssParser().parse(feed_response(feed), unit)
    assert items[0].item_id_raw == ""


def test_parser_tolerates_a_missing_date(unit):
    feed = RSS_2_0.replace(
        "<pubDate>Mon, 28 Sep 2026 11:45:00 +0000</pubDate>", ""
    )
    items = RssParser().parse(feed_response(feed), unit)
    assert items[0].published_at is None
    assert items[0].published_precision == PRECISION_UNKNOWN


def test_parser_tolerates_a_missing_description(unit):
    feed = RSS_2_0.replace("<description>Short lead</description>", "")
    items = RssParser().parse(feed_response(feed), unit)
    assert items[0].summary == ""
    assert items[0].title == "First article"


def test_parser_tolerates_a_missing_enclosure(unit):
    feed = RSS_2_0.replace(
        '<enclosure url="https://e.example/a.jpg" type="image/jpeg" length="1234" />', ""
    )
    items = RssParser().parse(feed_response(feed), unit)
    assert items[0].enclosure_url == ""


def test_parser_drops_an_entry_with_no_content_at_all(unit):
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>T</title>
      <item><link>https://e.example/empty</link></item>
    </channel></rss>
    """
    assert RssParser().parse(feed_response(feed), unit) == []


def test_one_bad_entry_does_not_kill_the_feed(unit, monkeypatch):
    """Per-entry containment: a single malformed entry must not lose the rest."""
    original = RssParser._entry

    calls = {"n": 0}

    def flaky(self, entry, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("boom")
        return original(self, entry, **kwargs)

    monkeypatch.setattr(RssParser, "_entry", flaky)
    items = RssParser().parse(feed_response(RSS_2_0), unit)
    assert [item.title for item in items] == ["Second article"]


# --- parser: malformed input ----------------------------------------------
def test_parser_rejects_malformed_xml(unit):
    with pytest.raises(ParsingError):
        RssParser().parse(feed_response(MALFORMED), unit)


def test_parser_rejects_xml_that_is_not_a_feed(unit):
    with pytest.raises(ParsingError):
        RssParser().parse(feed_response("<html><body>nope</body></html>"), unit)


def test_parser_rejects_an_empty_body(unit):
    with pytest.raises(ParsingError):
        RssParser().parse(feed_response(""), unit)


def test_parser_does_not_touch_storage_or_network(unit):
    parser = RssParser()
    assert not hasattr(parser, "save")
    assert not hasattr(parser, "get")
    assert not hasattr(parser, "dedupe")


# --- time interpretation --------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected_iso,expected_precision",
    [
        # RFC 822 with an offset -> UTC, precision from the string.
        ("Mon, 28 Sep 2026 11:45:00 +0000", "2026-09-28T11:45:00+00:00", PRECISION_SECOND),
        ("Mon, 28 Sep 2026 11:45:00 GMT", "2026-09-28T11:45:00+00:00", PRECISION_SECOND),
        # ISO with a zone.
        ("2026-09-28T11:45:00+09:00", "2026-09-28T02:45:00+00:00", PRECISION_SECOND),
        ("2026-09-28T11:45:00Z", "2026-09-28T11:45:00+00:00", PRECISION_SECOND),
        # Date only -> day precision, NOT midnight.
        ("2026-09-28", "2026-09-28", PRECISION_DAY),
        # Naive: no zone invented.
        ("2026-09-28T11:45:00", "2026-09-28T11:45:00", PRECISION_SECOND),
        # Hour / minute granularity. The seconds are filled in by ``fromisoformat``
        # because a ``datetime`` always has them; the *precision* is what records
        # that the source never said them.
        ("2026-09-28T11:00:00", "2026-09-28T11:00:00", PRECISION_SECOND),
        ("2026-09-28 11:00", "2026-09-28T11:00:00", PRECISION_HOUR),
        ("2026-09-28 11:45", "2026-09-28T11:45:00", PRECISION_MINUTE),
        # Unparseable: nothing invented, the source's string is kept elsewhere.
        ("sometime last week", None, PRECISION_UNKNOWN),
        ("", None, PRECISION_UNKNOWN),
    ],
)
def test_feed_time_interpretation(raw, expected_iso, expected_precision):
    assert parse_feed_time(raw) == (expected_iso, expected_precision)


def test_a_date_only_value_is_never_promoted_to_midnight():
    """DR-5: 'published on that day' must stay distinguishable from 00:00."""
    iso, precision = parse_feed_time("2026-09-28")
    assert iso == "2026-09-28"
    assert "T00:00" not in iso
    assert precision == PRECISION_DAY


def test_a_naive_time_is_not_labelled_utc():
    """DR-13 / risk R2: the reference pipeline here reads JST and calls it UTC."""
    iso, _ = parse_feed_time("2026-09-28T11:45:00")
    assert iso is not None
    assert "+00:00" not in iso and not iso.endswith("Z")


# --- normalizer against the real feed -------------------------------------
def test_real_feed_parses_and_normalizes(unit):
    raw = RssParser().parse(feed_response(VNOVEL_RSS), unit)
    items = RssNormalizer().normalize(raw)

    assert len(raw) == 24
    assert len(items) > 0
    assert all(item.source_id == "vnovel" for item in items)
    assert all(item.is_valid() for item in items)


def test_real_feed_guid_repeats_the_link_so_identity_is_url_based(unit):
    """24 of 24 items have ``guid == link``. That is not an identifier."""
    raw = RssParser().parse(feed_response(VNOVEL_RSS), unit)
    assert all(item.item_id_raw == item.link for item in raw if item.item_id_raw)

    items = RssNormalizer().normalize(raw)
    assert all(item.metadata["identity_basis"] == "url" for item in items)


def test_real_feed_contains_a_duplicate_which_collapses_to_one_identity(unit):
    """The feed really does list one URL twice; identity is what fixes it."""
    raw = RssParser().parse(feed_response(VNOVEL_RSS), unit)
    items = RssNormalizer().normalize(raw)

    keys = [item.identity_key for item in items]
    assert len(set(keys)) < len(keys)
    assert len(set(keys)) == len({key for key in keys})


def test_real_feed_items_without_a_date_are_still_accepted(unit):
    """Only 12 of 24 items carry a date; the contract must not demand one."""
    raw = RssParser().parse(feed_response(VNOVEL_RSS), unit)
    undated = [item for item in raw if item.published_at is None]
    assert undated, "the real feed is expected to contain undated items"

    items = RssNormalizer().normalize(raw)
    assert all(item.is_valid() for item in items)


def test_real_feed_enclosures_become_media(unit):
    raw = RssParser().parse(feed_response(VNOVEL_RSS), unit)
    with_enclosure = [item for item in raw if item.enclosure_url]
    assert len(with_enclosure) == 21

    items = RssNormalizer().normalize(raw)
    assert any(item.media for item in items)


def test_real_feed_titles_survive_intact(unit):
    """A bracketed prefix is part of the title, not a publisher marker."""
    raw = RssParser().parse(feed_response(VNOVEL_RSS), unit)
    items = RssNormalizer().normalize(raw)
    assert any(item.title and item.title.startswith("[") for item in items)


def test_real_feed_publisher_is_the_feed_name(unit):
    raw = RssParser().parse(feed_response(VNOVEL_RSS), unit)
    items = RssNormalizer().normalize(raw)
    publishers = {item.publisher for item in items}
    # The feed names itself in <channel><title>; that is what a reader shows as
    # the origin, and it is what ``publisher`` means for a web source.
    assert publishers == {"Japanese Game Media RSS"}


# --- end to end: RSS -> Parser -> Normalizer -> Storage -------------------
def test_rss_chain_reaches_storage(unit, tmp_path):
    """The Phase 2 acceptance path for a web/RSS source."""
    storage = JsonlStorage(data_dir=tmp_path / "data")

    raw = RssParser().parse(feed_response(VNOVEL_RSS), unit)
    items = RssNormalizer().normalize(raw)
    report = storage.save(items)

    unique = {item.identity_key for item in items}
    assert report.source_id == "vnovel"
    # The real feed lists one URL twice, so 24 records are 23 distinct items.
    assert len(items) == 24
    assert report.inserted == len(unique) == 23
    assert report.duplicates == 1
    assert storage.known_keys("vnovel") == unique

    stored = storage.item_path("vnovel").read_text(encoding="utf-8").splitlines()
    assert len([line for line in stored if line]) == len(unique)


def test_rss_chain_is_incremental(unit, tmp_path):
    """Re-running the same feed must add nothing."""
    storage = JsonlStorage(data_dir=tmp_path / "data")
    items = RssNormalizer().normalize(RssParser().parse(feed_response(VNOVEL_RSS), unit))

    first = storage.save(items)
    second = storage.save(items)

    assert first.inserted == 23
    assert second.inserted == 0
    assert second.duplicates == 24


def test_rss_items_reload_from_disk(unit, tmp_path):
    from domain.models.item import NormalizedItem

    storage = JsonlStorage(data_dir=tmp_path / "data")
    items = RssNormalizer().normalize(RssParser().parse(feed_response(VNOVEL_RSS), unit))
    storage.save(items)

    import json

    lines = [
        json.loads(line)
        for line in storage.item_path("vnovel").read_text(encoding="utf-8").splitlines()
        if line
    ]
    reloaded = [NormalizedItem.from_dict(line) for line in lines]
    # Stored order is insertion order with the duplicate collapsed, so compare
    # as sets of identities rather than as lists.
    assert {item.identity_key for item in reloaded} == {item.identity_key for item in items}
    assert all(item.is_valid() for item in reloaded)


def test_rss_identity_does_not_collide_with_an_x_tweet(unit, tmp_path):
    """``vnovel:84674`` and ``x:84674`` are different items."""
    from domain.models.item import NormalizedItem

    storage = JsonlStorage(data_dir=tmp_path / "data")
    items = RssNormalizer().normalize(RssParser().parse(feed_response(VNOVEL_RSS), unit))
    storage.save(items)

    clash = NormalizedItem(
        source_id="x", item_id=items[0].item_id, content="a tweet",
        fetched_at=FETCHED.isoformat(),
    )
    report = storage.save([clash])

    assert report.inserted == 1
    assert clash.identity_key in storage.known_keys("x")
    assert clash.identity_key not in storage.known_keys("vnovel")
