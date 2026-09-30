"""Route -> parser bindings, and the normalizer kind each route feeds.

A provider says *where* the bytes come from; a parser says *how to read* them; a
normalizer says *how to turn a raw record into the shared contract*. None of the
three is allowed to know about the others (architecture freeze, section 8), so
the mapping between them has to live somewhere neutral. This module is that
somewhere.

Why this exists at all
----------------------
X-rss hard-codes the coupling inside its runner: it constructs a fetcher and
its matching parser in the same branch, so the two can never be reused
independently. Here the runner asks for a parser by ``(provider, route)`` and
never imports a parser class directly. Consequences:

* adding a provider touches exactly two files — the provider module and this
  table (plus ``providers/factory.py`` for construction);
* a test can exercise any parser without a provider, and vice versa;
* ``bindings()`` is a truthful, introspectable description of what the system
  can actually do.

The table is deliberately explicit rather than derived by convention. Two
providers can share a parser (``xtf`` reuses the HTML parser because its search
route returns the same Nitter markup), and a silent naming convention would make
that invisible.

Why the normalizer kind is bound here too
-----------------------------------------
The kind (``x`` / ``rss`` / ``youtube``) is what selects a Normalizer, and it is a
property of the *route*, not of the provider. Binding it on the provider name
would let the same content acquire two identities depending on which provider
answered first — exactly the failure the freeze's identity rules exist to
prevent.
"""
from __future__ import annotations

from dataclasses import dataclass

from parsers.base import BaseParser
from parsers.nitter_html import NitterHtmlParser
from parsers.nitter_rss import NitterRssParser
from parsers.rss import RssParser
from parsers.youtube import YoutubeParser


@dataclass(frozen=True, slots=True)
class Binding:
    """One route: the parser that reads it, and the normalizer kind it feeds."""

    parser: type[BaseParser]
    normalizer_kind: str


#: ``(provider name, route name) -> Binding``.
_BINDINGS: dict[tuple[str, str], Binding] = {
    # Nitter's own two routes (X-rss's fetchers/nitter.py reaches the same URLs).
    ("nitter", "rss"): Binding(NitterRssParser, "x"),
    ("nitter", "html"): Binding(NitterHtmlParser, "x"),
    # x-tweet-fetcher reaches the timeline through /search?q=from:...&f=tweets,
    # which renders the very same `div.timeline-item` markup as /{username}.
    # Sharing the parser is the correct call: identical bytes, identical reader.
    ("xtf", "search"): Binding(NitterHtmlParser, "x"),
    # Generic RSS/Atom input. One route, any number of configured feeds; the feed
    # URL is the tracked unit, so there is nothing else to bind.
    ("rss", "feed"): Binding(RssParser, "rss"),
    # YouTube channel feed. Same shape as RSS (one route, many configured
    # channels) but a different parser and a different normalizer kind: the
    # document is a YouTube Atom feed with ``yt:``/``media:`` payload, not a
    # generic feed, and its identity is ``yt:videoId`` rather than a URL.
    ("youtube", "feed"): Binding(YoutubeParser, "youtube"),
}

# Parsers are stateless and pure, so one instance per class is enough and
# avoids re-parsing selector tables on every account.
_CACHE: dict[type[BaseParser], BaseParser] = {}


def parser_for(provider: str, route: str) -> BaseParser | None:
    """Return the parser responsible for ``provider``'s ``route``.

    ``None`` means "no parser is bound" — a wiring bug, not a runtime failure,
    so the caller decides how loudly to complain.
    """
    binding = _BINDINGS.get((provider, route))
    if binding is None:
        return None
    instance = _CACHE.get(binding.parser)
    if instance is None:
        instance = binding.parser()
        _CACHE[binding.parser] = instance
    return instance


def normalizer_kind_for(provider: str, route: str) -> str | None:
    """The normalizer kind a route feeds, or ``None`` when unbound."""
    binding = _BINDINGS.get((provider, route))
    return binding.normalizer_kind if binding else None


def bindings() -> list[tuple[str, str, str]]:
    """Every known binding as ``(provider, route, parser name)``, sorted."""
    return sorted(
        (provider, route, binding.parser.name)
        for (provider, route), binding in _BINDINGS.items()
    )


def kinds() -> list[tuple[str, str, str]]:
    """Every binding as ``(provider, route, normalizer kind)``, sorted."""
    return sorted(
        (provider, route, binding.normalizer_kind)
        for (provider, route), binding in _BINDINGS.items()
    )


def unbound_routes(providers) -> list[str]:
    """Routes exposed by live providers that have no parser.

    Called once at start-up so a misconfiguration is reported *before* any
    network traffic, instead of surfacing as a mysterious empty run.
    """
    missing: list[str] = []
    for provider in providers:
        for route in provider.routes():
            if (provider.name, route) not in _BINDINGS:
                missing.append(f"{provider.name}/{route}")
    return missing
