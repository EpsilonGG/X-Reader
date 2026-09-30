"""x-tweet-fetcher adapter — optional second fetching route.

Relationship to the reference project (architecture freeze, section 2.2 and 7):

    X-Reader
        ├── Provider Interface
        ├── NitterProvider      (X-Reader's own, always available)
        └── XtfAdapter          <-- this module
                 └── x-tweet-fetcher   (optional, imported if present)

Why an adapter rather than a copy: x-tweet-fetcher is not vendored and not a
declared dependency. When the package is importable the adapter uses it; when it
is not, X-Reader runs exactly as before. That keeps the reference project out of
X-Reader's core dependency boundary, which is precisely what the freeze asks for.

Which part of x-tweet-fetcher is used, and why:

* ``xtf.http.get_text`` / ``probe`` and ``xtf.exceptions`` — the *transport*
  capability: bounded retry with backoff, a never-raising reachability probe,
  and typed failures. This is the reusable "fetching approach" the freeze refers
  to.
* ``xtf.config.nitter_instances`` — instance list parsing, used only when
  X-Reader's own config does not supply instances.

Deliberately **not** used: ``xtf.Router.fetch_timeline`` and the ``xtf`` data
models. Router returns already-normalized ``Tweet`` objects, which would

1. bypass X-Reader's Parser layer entirely, collapsing the Fetcher/Parser
   boundary the freeze mandates, and
2. discard the raw payload, breaking the raw-data-first principle — the whole
   point of Phase 1 storage is that a future Normalizer can re-derive a better
   model from what was actually fetched.

So the adapter keeps x-tweet-fetcher on the *fetch* side of the boundary and
hands back a plain :class:`RawResponse`.

What this route adds over the Nitter provider: x-tweet-fetcher reaches timelines
through ``/search?q=from:{username}&f=tweets`` rather than ``/{username}``. That
is a genuinely different upstream route, so it can succeed on instances where
the direct timeline route is broken.
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime
from datetime import timezone
from typing import Any

from domain.errors import NetworkError
from domain.errors import ProviderError
from domain.models.account import Account
from infrastructure.http.response import RawResponse
from providers.base import BaseProvider

ROUTE_SEARCH = "search"

#: Nitter's search page renders the same timeline markup as a profile page, so
#: this route is parsed by the same HTML parser as ``nitter/html``.
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class _XtfRuntime:
    """Holds the imported x-tweet-fetcher pieces, if they are available."""

    def __init__(self, config: Any, http: Any, exceptions: Any) -> None:
        self.config = config
        self.http = http
        self.exceptions = exceptions

    @classmethod
    def load(cls) -> _XtfRuntime | None:
        try:
            from xtf import config as xtf_config
            from xtf import exceptions as xtf_exceptions
            from xtf import http as xtf_http
        except Exception:
            return None
        return cls(xtf_config, xtf_http, xtf_exceptions)


class XtfAdapter(BaseProvider):
    """Fetch a timeline through x-tweet-fetcher's transport layer."""

    name = "xtf"
    routes_ = (ROUTE_SEARCH,)

    def __init__(
        self,
        instances: list[str] | None = None,
        timeout: int = 15,
        runtime: _XtfRuntime | None = None,
        probe_timeout: int = 3,
    ) -> None:
        self._runtime = runtime if runtime is not None else _XtfRuntime.load()
        self.timeout = timeout
        self.probe_timeout = probe_timeout
        self._explicit_instances = [
            (i or "").strip().rstrip("/") for i in (instances or []) if (i or "").strip()
        ]
        self._preferred: str | None = None

    # -- introspection ------------------------------------------------------
    @property
    def installed(self) -> bool:
        return self._runtime is not None

    def available(self) -> bool:
        return self.installed and bool(self._instances())

    def _instances(self) -> list[str]:
        if self._explicit_instances:
            return self._explicit_instances
        if self._runtime is None:
            return []
        try:
            return [i.rstrip("/") for i in self._runtime.config.nitter_instances()]
        except Exception:
            return []

    @property
    def preferred_endpoint(self) -> str | None:
        return self._preferred

    # -- error mapping ------------------------------------------------------
    def _translate(self, exc: Exception) -> ProviderError:
        """Map an xtf exception onto X-Reader's error taxonomy."""
        exceptions = self._runtime.exceptions if self._runtime else None
        code = getattr(exc, "code", "")
        message = f"xtf: {exc}"

        if exceptions is not None:
            if isinstance(exc, exceptions.NotFound):
                return ProviderError(message, status=404)
            if isinstance(exc, (exceptions.RateLimited, exceptions.UpstreamDown,
                                exceptions.BackendUnavailable)):
                return NetworkError(message)
        if code in ("not_found",):
            return ProviderError(message, status=404)
        if code in ("rate_limited", "upstream_down", "backend_unavailable"):
            return NetworkError(message)
        return ProviderError(message)

    # -- BaseProvider -------------------------------------------------------
    def fetch(self, account: Account, route: str) -> RawResponse:
        if route != ROUTE_SEARCH:
            raise ProviderError(f"xtf: unknown route '{route}'")
        if self._runtime is None:
            raise ProviderError(
                "xtf: x-tweet-fetcher is not installed; "
                "install it or remove 'xtf' from provider.order"
            )

        instances = self._instances()
        if not instances:
            raise ProviderError("xtf: no Nitter instances configured")

        ordered = self._ordered(instances)
        causes: dict[str, str] = {}

        for instance in ordered:
            url = self._search_url(instance, account)
            try:
                text = self._runtime.http.get_text(
                    url, headers=_HEADERS, timeout=self.timeout
                )
            except Exception as exc:  # noqa: BLE001 - translated immediately
                translated = self._translate(exc)
                causes[instance] = f"{translated.kind}: {translated}"
                if translated.status == 404:
                    break
                continue

            self._preferred = instance
            return RawResponse(
                url=url,
                status_code=200,
                content_type="text/html",
                text=text,
                fetched_at=datetime.now(timezone.utc),
            )

        detail = "; ".join(f"{ep} -> {why}" for ep, why in causes.items()) or "no instances"
        raise ProviderError(
            f"xtf/{route} failed for {account.display} "
            f"({len(causes)}/{len(instances)} instances tried): {detail}"
        )

    # -- internals ----------------------------------------------------------
    def _ordered(self, instances: list[str]) -> list[str]:
        if self._preferred and self._preferred in instances:
            rest = [i for i in instances if i != self._preferred]
            return [self._preferred, *rest]
        return list(instances)

    @staticmethod
    def _search_url(instance: str, account: Account) -> str:
        # Same query shape x-tweet-fetcher uses for a user timeline.
        params = urllib.parse.urlencode({"q": f"from:{account.username}", "f": "tweets"})
        return f"{instance}/search?{params}"
