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

import re

from domain.errors import ProviderError
from domain.models.account import Account
from infrastructure.http.client import HTTPClient
from infrastructure.http.response import RawResponse
from providers.base import BaseProvider

ROUTE_RSS = "rss"
ROUTE_HTML = "html"

# A 200 is not proof of a feed. Public instances increasingly sit behind
# anti-bot interstitials (Anubis, Cloudflare) that answer 200 with an HTML
# challenge page. Accepting that as success is worse than a hard failure: the
# run reports "0 tweets" for every account and the instance loop stops at the
# first challenger, so the one instance that *would* have worked is never tried.
_CHALLENGE = re.compile(
    r"making sure you[’']?re not a bot|anubis|cf-browser-verification"
    r"|just a moment\.\.\.|checking your browser|enable javascript and cookies",
    re.IGNORECASE,
)


def _looks_like_payload(route: str, text: str) -> bool:
    """Whether `text` is the thing this route is supposed to return."""
    head = text[:4096]
    if _CHALLENGE.search(head):
        return False
    if route == ROUTE_RSS:
        # Nitter's RSS always carries the channel wrapper, even for an account
        # with no tweets in the window.
        return "<rss" in text or "<channel" in text
    # The HTML route: a real timeline (even an empty one) has the container.
    return 'class="timeline' in text or "timeline-item" in text


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

            if not _looks_like_payload(route, response.text):
                # 200 + a challenge page or an error page. Treat it as this
                # instance failing, not as "this account has no tweets".
                causes[endpoint] = "challenge/interstitial (200 but not a payload)"
                continue

            self._preferred = endpoint
            return response
        detail = "; ".join(f"{ep} -> {why}" for ep, why in causes.items()) or "no endpoints"
        raise ProviderError(
            f"nitter/{route} failed for {account.display} "
            f"({len(causes)}/{len(self.endpoints)} instances tried): {detail}"
        )
