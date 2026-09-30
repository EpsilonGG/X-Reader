"""Generic RSS provider: fetch a configured feed URL.

The whole of X-Reader's "website / RSS" input capability. There is no
``visualnovel_provider.py``: any project that can emit standard RSS becomes a
source simply by having its URL listed in ``rss_sources``
(``config/config.yaml``).

Boundary (freeze, section 7): a provider owns *how to reach* a source — HTTP,
timeout, retry, failover, error mapping. It returns a :class:`RawResponse` and
nothing else. It does not parse, normalise, deduplicate or store.

Tracked-unit note
-----------------
``BaseProvider.fetch`` takes the tracked unit's identifier. Phase 1 only had one
kind of unit (an X account), so the parameter is typed ``Account``. An RSS source
is passed through the same slot with its config ``id`` as the identifier; the
Provider/Parser interface is frozen (task section 20) and is deliberately not
changed for this. A future ``TrackedUnit`` abstraction would be a Phase 3
refactor, not something to slip in here.
"""
from __future__ import annotations

from config.schema import RssSourceConfig
from domain.errors import ProviderError
from domain.models.account import Account
from infrastructure.http.client import HTTPClient
from infrastructure.http.response import RawResponse
from providers.base import BaseProvider

#: The single route a feed URL exposes. Kept explicit so the
#: ``(provider, route) -> parser`` binding stays truthful and introspectable.
ROUTE_FEED = "feed"


class RssProvider(BaseProvider):
    """Fetches the feed URLs declared in ``rss_sources``."""

    name = "rss"
    routes_ = (ROUTE_FEED,)

    def __init__(self, sources: list[RssSourceConfig], client: HTTPClient) -> None:
        self.client = client
        self._by_id = {source.id: source for source in sources}

    def available(self) -> bool:
        """Usable when at least one enabled source has a URL."""
        return any(
            source.enabled and source.url for source in self._by_id.values()
        )

    def sources(self) -> list[RssSourceConfig]:
        return list(self._by_id.values())

    def fetch(self, unit: Account, route: str) -> RawResponse:
        if route != ROUTE_FEED:
            raise ProviderError(f"rss: unknown route '{route}'")

        source_id = unit.username
        source = self._by_id.get(source_id)
        if source is None:
            # A wiring bug rather than an upstream failure, but it surfaces
            # through the same channel so one bad source cannot stop a run.
            raise ProviderError(f"rss: no configured source with id '{source_id}'")
        if not source.url:
            raise ProviderError(f"rss: source '{source_id}' has no url")

        # HTTPClient already maps timeouts, retryable statuses and fatal statuses
        # onto the error taxonomy, so there is nothing to add here.
        return self.client.get(source.url)
