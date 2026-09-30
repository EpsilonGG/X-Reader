"""Nitter provider — X-Reader's own default fetching route.

Lineage:

* **X-rss** (`fetchers/nitter.py`) — the two routes themselves:
  ``GET /{username}/rss`` and ``GET /{username}``. These are proven to work
  against public instances, so they are kept unchanged.
* **X-rss** (`app/endpoint_pool.py`) — "remember the instance that worked and
  put it first next time". Kept, but made per-provider and, unlike X-rss's
  ``deque`` rotation, persisted for the whole run so later accounts benefit.
* **x-tweet-fetcher** (`backends/nitter.py`) — the failover *loop*: try each
  instance in order, fall through on rate-limit / upstream errors, and report
  every instance's failure if none worked. Adopted because GitHub Actions runs
  from datacenter IPs where a single instance failing is routine.

Improvement over both: a 404 short-circuits the instance loop. X-rss would try
every remaining endpoint for a non-existent account; xtf would too. A 404 means
the account is not there, so trying eleven more instances only burns the
workflow's time budget.
"""
from __future__ import annotations

from domain.errors import ProviderError
from domain.models.account import Account
from infrastructure.http.client import HTTPClient
from infrastructure.http.response import RawResponse
from providers.base import BaseProvider

ROUTE_RSS = "rss"
ROUTE_HTML = "html"


class NitterProvider(BaseProvider):
    """Fetch a Nitter timeline over one or more public/self-hosted instances."""

    name = "nitter"
    routes_ = (ROUTE_RSS, ROUTE_HTML)

    def __init__(
        self,
        endpoints: list[str],
        client: HTTPClient,
        routes: tuple[str, ...] = routes_,
    ) -> None:
        cleaned: list[str] = []
        for raw in endpoints:
            endpoint = (raw or "").strip().rstrip("/")
            if endpoint and endpoint not in cleaned:
                cleaned.append(endpoint)
        if not cleaned:
            raise ValueError("NitterProvider requires at least one endpoint")
        self.endpoints = cleaned
        self.client = client
        self.routes_ = tuple(routes)
        self._preferred: str | None = None

    # -- introspection ------------------------------------------------------
    @property
    def preferred_endpoint(self) -> str | None:
        """The instance that last succeeded, if any (useful in the run report)."""
        return self._preferred

    def available(self) -> bool:
        return bool(self.endpoints)

    # -- internals ----------------------------------------------------------
    def _ordered_endpoints(self) -> list[str]:
        if self._preferred and self._preferred in self.endpoints:
            rest = [e for e in self.endpoints if e != self._preferred]
            return [self._preferred, *rest]
        return list(self.endpoints)

    @staticmethod
    def _url_for(endpoint: str, account: Account, route: str) -> str:
        if route == ROUTE_RSS:
            return f"{endpoint}/{account.username}/rss"
        if route == ROUTE_HTML:
            return f"{endpoint}/{account.username}"
        raise ProviderError(f"nitter: unknown route '{route}'")

    # -- BaseProvider -------------------------------------------------------
    def fetch(self, account: Account, route: str) -> RawResponse:
        causes: dict[str, str] = {}

        for endpoint in self._ordered_endpoints():
            url = self._url_for(endpoint, account, route)
            try:
                response = self.client.get(url)
            except ProviderError as exc:
                causes[endpoint] = f"{exc.kind}: {exc}"
                if exc.status == 404:
                    # The account does not exist on this instance's upstream;
                    # other instances will report the same thing.
                    break
                continue

            self._preferred = endpoint
            return response

        detail = "; ".join(f"{ep} -> {why}" for ep, why in causes.items()) or "no endpoints"
        raise ProviderError(
            f"nitter/{route} failed for {account.display} "
            f"({len(causes)}/{len(self.endpoints)} instances tried): {detail}"
        )
