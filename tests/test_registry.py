"""Registry tests.

The registry is what keeps providers and parsers from knowing about each other.
The most valuable test here is the last one: every route a live provider
advertises must have a parser. That single assertion turns "the run silently
collected nothing" into a failing test.

Since Phase 2 the registry also carries the **normalizer kind** for each route,
which is what stops the same content acquiring two identities depending on which
provider happened to answer first.
"""
from __future__ import annotations

from app.registry import bindings
from app.registry import kinds
from app.registry import normalizer_kind_for
from app.registry import parser_for
from app.registry import unbound_routes
from config.schema import RssSourceConfig
from config.schema import YoutubeChannelConfig
from parsers.nitter_html import NitterHtmlParser
from parsers.nitter_rss import NitterRssParser
from parsers.rss import RssParser
from parsers.youtube import YoutubeParser
from providers.nitter import NitterProvider
from providers.rss import RssProvider
from providers.xtf_adapter import XtfAdapter
from providers.youtube import YoutubeProvider


def test_known_bindings_resolve():
    assert isinstance(parser_for("nitter", "rss"), NitterRssParser)
    assert isinstance(parser_for("nitter", "html"), NitterHtmlParser)
    # The xtf search route returns the same Nitter markup, so it shares a parser.
    assert isinstance(parser_for("xtf", "search"), NitterHtmlParser)
    assert isinstance(parser_for("rss", "feed"), RssParser)
    # YouTube gets its own parser: same XML family, different document.
    assert isinstance(parser_for("youtube", "feed"), YoutubeParser)


def test_unknown_binding_returns_none():
    assert parser_for("nitter", "telepathy") is None
    assert parser_for("mystery", "html") is None


def test_parsers_are_cached_and_reused():
    assert parser_for("nitter", "html") is parser_for("nitter", "html")


def test_bindings_are_sorted_and_complete():
    assert bindings() == [
        ("nitter", "html", "nitter_html"),
        ("nitter", "rss", "nitter_rss"),
        ("rss", "feed", "rss"),
        ("xtf", "search", "nitter_html"),
        ("youtube", "feed", "youtube"),
    ]


# --- normalizer kind binding ----------------------------------------------
def test_normalizer_kind_is_a_property_of_the_route():
    """Two providers, one kind: the same tweet must not get two identities."""
    assert normalizer_kind_for("nitter", "rss") == "x"
    assert normalizer_kind_for("nitter", "html") == "x"
    assert normalizer_kind_for("xtf", "search") == "x"
    assert normalizer_kind_for("rss", "feed") == "rss"
    # YouTube is bound on the route, so a future second YouTube provider
    # (a mirror, say) would feed the same kind and the same identity rule.
    assert normalizer_kind_for("youtube", "feed") == "youtube"


def test_unknown_route_has_no_normalizer_kind():
    assert normalizer_kind_for("nitter", "telepathy") is None


def test_kinds_are_sorted_and_complete():
    assert kinds() == [
        ("nitter", "html", "x"),
        ("nitter", "rss", "x"),
        ("rss", "feed", "rss"),
        ("xtf", "search", "x"),
        ("youtube", "feed", "youtube"),
    ]


def test_every_bound_parser_has_a_normalizer_kind():
    """A binding with a parser but no kind would normalize with the wrong model."""
    for _provider, _route, kind in kinds():
        assert kind in ("x", "rss", "youtube")


# --- coverage of live providers -------------------------------------------
def test_every_live_provider_route_has_a_parser():
    """A provider route with no parser would silently collect nothing."""
    providers = [
        NitterProvider(endpoints=["https://a.example"], client=None),
        XtfAdapter(runtime=None),
        RssProvider(
            sources=[RssSourceConfig(id="vnovel", url="https://v.example/rss.xml")],
            client=None,
        ),
        YoutubeProvider(
            channels=[YoutubeChannelConfig(channel_id="UCabcdefghijklmnopqrstuv")],
            client=None,
        ),
    ]
    assert unbound_routes(providers) == []


def test_unbound_routes_detects_a_gap():
    class RogueProvider:
        name = "rogue"

        def routes(self):
            return ("mystery",)

    assert unbound_routes([RogueProvider()]) == ["rogue/mystery"]
