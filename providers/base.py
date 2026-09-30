"""Provider contract — the single extension point for new data sources.

Boundary (architecture freeze, sections 6 / 7 / 8):

* A **Provider** owns *how to reach* a source. It knows about endpoints,
  routes, failover and HTTP. It returns a :class:`RawResponse` and nothing else.
* A **Parser** owns *how to understand* the bytes. It knows about markup and
  returns :class:`RawTweet` records.
* Neither knows about storage, RSS, summaries, scheduling or AI.

This is what makes the freeze's "replace one fetching method without touching
Storage / Normalizer / downstream" requirement achievable: swapping in a new
source means adding one Provider plus its route→parser binding, nothing else.
"""
from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from domain.models.account import Account
from infrastructure.http.response import RawResponse


class BaseProvider(ABC):
    """A source of raw X data."""

    #: Stable identifier used in config (`provider.order`) and in stored records.
    name: str = "base"

    #: The route names this provider exposes, in the order they should be tried.
    routes_: tuple[str, ...] = ()

    def routes(self) -> tuple[str, ...]:
        return self.routes_

    def available(self) -> bool:
        """Whether this provider can be used at all in the current environment.

        Never raises. A provider that depends on an optional package returns
        ``False`` here instead of failing during a run.
        """
        return True

    @abstractmethod
    def fetch(self, account: Account, route: str) -> RawResponse:
        """Fetch ``account`` through ``route``.

        Raises :class:`~domain.errors.ProviderError` (or its subclass
        :class:`~domain.errors.NetworkError`) on failure. Implementations must
        never return a response they know to be unusable.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release provider-owned resources. Never raises."""

    def __enter__(self) -> BaseProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
