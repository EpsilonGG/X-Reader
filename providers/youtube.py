"""YouTube provider: fetch a configured channel's Atom feed.

The whole of X-Reader's YouTube input capability, and deliberately the **no-API**
route: ``https://www.youtube.com/feeds/videos.xml?channel_id=<id>`` is a public
Atom document that needs no key, no quota, no OAuth and no third-party service.
That matters because X-Reader runs on free GitHub Actions, where "add an API key
and a paid quota" is not an available answer.

Why not the YouTube Data API
----------------------------
The Data API needs a key, a quota and a secret to rotate; it would also make the
core acquisition path depend on a credential, which the freeze forbids. The feed
is the same network model as the RSS provider — one GET, one document — so it
reuses :class:`~infrastructure.http.client.HTTPClient` unchanged (timeout,
bounded retry, typed errors).

Why this is not the RSS provider
--------------------------------
Both reach an XML document, and that is where the similarity ends. YouTube's feed
is a distinct source with its own identity rule (``<yt:videoId>``, not a URL),
its own namespace (``media:``, not ``<description>``) and its own tracked unit
(a channel, not a feed URL). Folding it into ``RssProvider`` would mean the RSS
provider had to know which of two unrelated documents it was holding — the
"both are XML" shortcut the architecture forbids.

Boundary (freeze section 7): a provider owns *how to reach* a source — HTTP,
timeout, retry, failover, error mapping. It returns a :class:`RawResponse` and
nothing else. It does not parse, normalise, deduplicate or store.
"""
from __future__ import annotations

from config.schema import YoutubeChannelConfig
from domain.errors import ProviderError
from domain.models.account import Account
from infrastructure.http.client import HTTPClient
from infrastructure.http.response import RawResponse
from providers.base import BaseProvider

#: The single route a channel exposes. Kept explicit so the
#: ``(provider, route) -> parser`` binding stays truthful and introspectable.
ROUTE_FEED = "feed"

#: Public Atom feed for a channel. No API key, no quota.
FEED_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def feed_url_for(channel_id: str) -> str:
    """The public channel feed URL for ``channel_id``."""
    return FEED_URL_TEMPLATE.format(channel_id=channel_id)


class YoutubeProvider(BaseProvider):
    """Fetches the channel feeds declared in ``youtube_channels``."""

    name = "youtube"
    routes_ = (ROUTE_FEED,)

    def __init__(self, channels: list[YoutubeChannelConfig], client: HTTPClient) -> None:
        self.client = client
        self._by_id = {channel.channel_id: channel for channel in channels}

    def available(self) -> bool:
        """Usable when at least one enabled channel has a channel id."""
        return any(channel.enabled and channel.channel_id for channel in self._by_id.values())

    def channels(self) -> list[YoutubeChannelConfig]:
        return list(self._by_id.values())

    def url_for(self, channel_id: str) -> str:
        """The URL this provider would fetch for ``channel_id``.

        Public so the resolution rule (explicit override, else the generated
        feed URL) is testable without a network round trip.
        """
        channel = self._by_id.get(channel_id)
        if channel is None:
            return ""
        return channel.url or feed_url_for(channel.channel_id)

    def fetch(self, unit: Account, route: str) -> RawResponse:
        if route != ROUTE_FEED:
            raise ProviderError(f"youtube: unknown route '{route}'")

        channel_id = unit.username
        channel = self._by_id.get(channel_id)
        if channel is None:
            # A wiring bug rather than an upstream failure, but it surfaces
            # through the same channel so one bad channel cannot stop a run.
            raise ProviderError(f"youtube: no configured channel with id '{channel_id}'")
        if not channel.channel_id:
            raise ProviderError(f"youtube: channel '{channel_id}' has no channel_id")

        # HTTPClient already maps timeouts, retryable statuses and fatal statuses
        # onto the error taxonomy, so there is nothing to add here.
        return self.client.get(self.url_for(channel_id))
