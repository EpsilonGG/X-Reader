"""Provider factory: turn validated configuration into provider instances.

Kept out of the runner so the runner stays orchestration-only (X-rss's runner
builds every object inline, which is why it grew into the busiest file in that
project). This is also the one place that knows how to construct each concrete
provider, so adding a provider means editing exactly two files: the provider
module and this factory.
"""
from __future__ import annotations

from config.schema import Config
from infrastructure.http.client import HTTPClient
from providers.base import BaseProvider
from providers.nitter import NitterProvider
from providers.rss import RssProvider
from providers.xtf_adapter import XtfAdapter
from providers.youtube import YoutubeProvider


def build_providers(config: Config, client: HTTPClient) -> list[BaseProvider]:
    """Build the providers listed in ``provider.order``.

    These serve **X units** (tracked accounts). Providers that report themselves
    unavailable (for example the xtf adapter when x-tweet-fetcher is not
    installed) are skipped rather than failing the run.
    """
    providers: list[BaseProvider] = []

    for name in config.provider.order:
        if name == "nitter":
            provider: BaseProvider = NitterProvider(
                endpoints=config.provider.nitter.endpoints,
                client=client,
                routes=tuple(config.provider.nitter.routes),
            )
        elif name == "xtf":
            if not config.provider.xtf.enabled:
                continue
            provider = XtfAdapter(
                # Empty instance list means "reuse the Nitter endpoints", so
                # instances have a single source of truth.
                instances=config.provider.xtf.instances or config.provider.nitter.endpoints,
                timeout=config.http.timeout,
            )
        else:  # pragma: no cover - config validation rejects unknown names
            continue

        if provider.available():
            providers.append(provider)

    return providers


def build_rss_provider(config: Config, client: HTTPClient) -> RssProvider | None:
    """Build the RSS provider, or ``None`` when no feed is configured.

    Deliberately not part of ``provider.order``: that list is the X fallback
    chain (one unit, several ways to reach it), whereas an RSS source *is* the
    unit. Mixing them would let the runner try Nitter for a feed URL.
    """
    if not config.enabled_rss_sources:
        return None
    provider = RssProvider(config.enabled_rss_sources, client)
    return provider if provider.available() else None


def build_youtube_provider(config: Config, client: HTTPClient) -> YoutubeProvider | None:
    """Build the YouTube provider, or ``None`` when no channel is configured.

    Same reasoning as :func:`build_rss_provider`, and for the same reason it is
    not part of ``provider.order``: a channel *is* the unit, so putting it in the
    X fallback chain would let the runner try Nitter for a YouTube feed.
    """
    if not config.enabled_youtube_channels:
        return None
    provider = YoutubeProvider(config.enabled_youtube_channels, client)
    return provider if provider.available() else None
