"""Storage contract.

Storage is the boundary between acquisition and everything downstream (freeze
v3, sections 11 / 12 / 16). Two rules define it:

1. **It knows one model: ``NormalizedItem``.** Not tweets, not feed entries, not
   providers, not parsers, not RSS, not Telegram. That is what makes it possible
   to replace an acquisition path without touching stored data, and to add an
   output without touching acquisition.
2. **It is the source of truth for "have I seen this?"** — and only for that.
   Whether an item has been *delivered* to some output is a different question
   with a different answer, and it lives in ``DeliveryRecord``. Conflating the
   two is how a project loses content: the reference aggregator in this workspace
   marks everything it fetches as "seen" but only publishes a capped prefix, so
   the remainder is never published and never retried (see
   ``docs/ARCHITECTURE_FREEZE_V3.md`` section 16).

The raw record archive is deliberately a *separate* interface
(:class:`RawRecordArchive`). Phase 1's raw tweets are evidence kept for
re-processing, not the canonical store, and nothing in the Phase 2 data flow
depends on them.

Read-back for output adapters is a third, also separate, interface
(:class:`ItemReader`) — added in Phase 3 for exactly the same reason: a
downstream consumer's need must not widen the acquisition contract.

Keeping all three behind interfaces is what lets a derived index (SQLite) be
added without touching a single caller.
"""
from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field

from domain.models.account import Account
from domain.models.item import NormalizedItem
from domain.models.tweet import RawTweet
from infrastructure.http.response import RawResponse

#: Delivery statuses. ``sent`` is the only terminal success.
DELIVERY_PENDING = "pending"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"
DELIVERY_SKIPPED = "skipped"


@dataclass(slots=True)
class SaveReport:
    """What happened when a batch of records was offered to storage."""

    received: int = 0
    inserted: int = 0
    duplicates: int = 0
    invalid: int = 0
    malformed_lines: int = 0
    path: str = ""
    #: The tracked unit this batch belonged to: an X handle, or a source id.
    unit: str = ""
    #: Set for NormalizedItem batches, empty for raw-record batches.
    source_id: str = ""

    def to_dict(self) -> dict:
        return {
            "received": self.received,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "invalid": self.invalid,
            "malformed_lines": self.malformed_lines,
            "path": self.path,
            "unit": self.unit,
            "source_id": self.source_id,
        }


@dataclass(slots=True)
class RunRecord:
    """One audit line: what a single (unit, provider, route) attempt did."""

    run_id: str
    #: The tracked unit: an X handle, or an RSS source id.
    account: str
    status: str  # ok | no_tweets | error
    #: ``x`` | ``rss`` | ``youtube`` — which normalizer handled this unit.
    kind: str = "x"
    source_id: str = ""
    provider: str = ""
    route: str = ""
    url: str = ""
    fetched: int = 0
    normalized: int = 0
    inserted: int = 0
    duplicates: int = 0
    error_kind: str | None = None
    error: str | None = None
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    attempts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "account": self.account,
            "status": self.status,
            "kind": self.kind,
            "source_id": self.source_id,
            "provider": self.provider,
            "route": self.route,
            "url": self.url,
            "fetched": self.fetched,
            "normalized": self.normalized,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "error_kind": self.error_kind,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
        }


@dataclass(slots=True)
class DeliveryRecord:
    """One output adapter's opinion about one item.

    Append-only, like everything else here. The latest record for an
    ``identity_key`` wins; the history stays so a failure can be explained.
    """

    output: str
    identity_key: str
    status: str
    detail: str = ""
    attempted_at: str = ""

    def to_dict(self) -> dict:
        return {
            "output": self.output,
            "identity_key": self.identity_key,
            "status": self.status,
            "detail": self.detail,
            "attempted_at": self.attempted_at,
        }


class BaseStorage(ABC):
    """Persist normalized items, run records, raw bodies and delivery state."""

    # -- canonical: NormalizedItem ------------------------------------------
    @abstractmethod
    def known_keys(self, source_id: str | None = None) -> set[str]:
        """Every ``identity_key`` already stored, optionally for one source."""

    @abstractmethod
    def save(self, items: list[NormalizedItem]) -> SaveReport:
        """Persist the items that are not stored yet.

        Must be idempotent: calling it twice with the same input inserts once.
        Records are never rewritten, so a stored item stays immutable.
        """

    @abstractmethod
    def record_run(self, record: RunRecord) -> None:
        """Append one audit record for a run attempt."""

    @abstractmethod
    def capture_raw(
        self, scope: Account | str, provider: str, route: str, response: RawResponse
    ) -> str | None:
        """Optionally keep the raw response body. Returns the path or ``None``."""

    # -- delivery state (separate from "seen") ------------------------------
    @abstractmethod
    def delivery_state(self, output: str) -> dict[str, str]:
        """``identity_key -> latest status`` for one output adapter."""

    @abstractmethod
    def pending_for(self, output: str, keys: list[str]) -> list[str]:
        """Of ``keys``, those this output has not successfully delivered yet."""

    @abstractmethod
    def mark_delivered(
        self,
        output: str,
        keys: list[str],
        status: str = DELIVERY_SENT,
        detail: str = "",
    ) -> int:
        """Record a delivery attempt for each key. Returns how many were written.

        Never touches the item store: a failed delivery must not remove an item,
        and a re-delivery must not need a refetch.
        """


class RawRecordArchive(ABC):
    """The Phase 1 raw-record archive: evidence, not the canonical store.

    Kept because the freeze requires that already-fetched data can be
    re-processed by a better Normalizer without refetching. It is a separate
    interface so that nothing in the Phase 2 data flow can depend on raw shapes
    leaking into storage.
    """

    @abstractmethod
    def known_ids(self, unit: Account | str) -> set[str]:
        """Every raw record id already archived for ``unit``."""

    @abstractmethod
    def archive_raw_tweets(self, unit: Account | str, tweets: list[RawTweet]) -> SaveReport:
        """Archive raw tweets for ``unit``. Idempotent, first write wins."""


class ItemReader(ABC):
    """Read normalized items back out of the store.

    A separate interface for the same reason :class:`RawRecordArchive` is one:
    ``BaseStorage`` is the *acquisition* contract and stays frozen. Phase 3's
    delivery retry needs something the acquisition path never needed — a failed
    delivery is retried on a **later run**, when the item is no longer in memory
    and must be read back from disk. Adding a method to ``BaseStorage`` would
    widen the acquisition contract for a downstream consumer's benefit, so the
    capability lives here instead.

    Read-only by construction: there is no write method, so an output adapter
    cannot use this to change what has been stored.
    """

    @abstractmethod
    def load_items(self, source_id: str | None = None) -> list[NormalizedItem]:
        """Every stored item, or only those in one ``source_id`` namespace.

        Order is not guaranteed to be meaningful; callers that care must sort.
        """
