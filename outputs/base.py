"""Output adapter contract (interface only in Phase 2).

The output layer is a **downstream consumer**. It is given normalized items and
reports what it managed to deliver. It does not fetch, does not modify items,
does not write business data, does not decide identity, does not deduplicate and
does not call providers (task section 13).

That last point is what keeps the dependency arrow pointing one way::

    NormalizedItem -> OutputAdapter -> delivery result

and never::

    OutputAdapter -> NormalizedItem -> ... -> Storage

Two consequences worth stating, because they are the reason this file exists at
all rather than Telegram being written directly:

1. **The adapter does not record delivery state.** It returns a
   :class:`DeliveryResult`; the caller persists it through Storage. An output
   adapter that wrote its own bookkeeping would be a second source of truth, and
   the freeze requires Storage to remain the only one.
2. **Formatting is a renderer concern, not a model concern.** Telegram renders an
   item as::

       [from @username](canonical_url)

       content

   and YouTube as ``[from YouTube / Channel](url)``, and a website as
   ``[from Website Name](url)``. All three are assembled from the *same* fields —
   ``publisher``, ``canonical_url``, ``content``. None of that markup belongs in
   ``NormalizedItem``: adding a ``telegram_text`` or ``markdown`` field would let
   one adapter's presentation dictate the shared model, and the model is what
   every other adapter and every stored record depends on.

No concrete adapter is implemented in Phase 2: Telegram, QQ/OneBot and RSS output
are all deferred (task sections 13 / 14 / 20).
"""
from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field

from domain.models.item import NormalizedItem

#: How many distinct failure reasons a :class:`DeliveryResult` spells out. Beyond
#: this the message stops being readable, and the count is reported instead.
MAX_DETAIL_PARTS = 3

#: Stop a delivery batch after this many consecutive item failures.
#:
#: Shared by every adapter, because the reasoning is about *sequential sends*,
#: not about any one protocol: a systemic problem — a dead network, a wrong
#: target, a revoked credential — costs one timeout per item, and a backlog of a
#: few hundred would exceed the workflow's 20-minute budget and get the job
#: killed mid-run, which risks the data the run just acquired. Aborting early
#: costs nothing: every unsent item keeps its pending state and is retried next
#: run, which is what would have happened anyway — just without burning the
#: budget to find out. A single success resets the counter, so one bad item
#: cannot poison the batch.
MAX_CONSECUTIVE_FAILURES = 5


def redact(text: str, *secrets: str) -> str:
    """Replace any secret with ``***`` before it can reach a log or a report.

    Shared because every adapter that carries a credential has the same leak:
    the secret ends up inside an exception string (a URL, a header, a repr), and
    an exception string is the one place it escapes without anyone noticing.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


@dataclass(slots=True)
class DeliveryResult:
    """What one adapter managed to do with one batch of items.

    Phase 3 note — the first real adapter forced this shape. Counts alone are
    not enough: delivery state is recorded per ``identity_key``, so a
    partially-successful batch must leave exactly the failed items pending
    (task section 10). The key lists are therefore the real state and the counts
    are derived from them, so the two can never disagree — the same reasoning
    that makes ``identity_key`` a property rather than a stored field.
    """

    output: str
    #: ``identity_key`` of every item this adapter successfully delivered.
    delivered_keys: list[str] = field(default_factory=list)
    #: ``identity_key`` of every item that failed. These stay pending, so the
    #: next run retries them without needing a refetch.
    failed_keys: list[str] = field(default_factory=list)
    #: Items the adapter declined to handle (unknown kind, permanently
    #: unsendable, ...). Deliberately *not* treated as delivered: an item an
    #: adapter cannot send must stay visible rather than be silently marked done.
    skipped_keys: list[str] = field(default_factory=list)
    #: Unique explanations in first-seen order. Not a public field — read
    #: :attr:`detail`. See :meth:`_add_detail` for why it is deduplicated.
    _details: list[str] = field(default_factory=list, repr=False)

    @property
    def detail(self) -> str:
        """Human-readable summary. Never contains a credential."""
        if not self._details:
            return ""
        summary = "; ".join(self._details[:MAX_DETAIL_PARTS])
        extra = len(self._details) - MAX_DETAIL_PARTS
        if extra > 0:
            summary += f"; … (+{extra} more distinct reason(s))"
        return summary

    def _add_detail(self, detail: str) -> None:
        """Record one explanation, at most once.

        A batch failure usually has *one* cause — a dead network, a wrong chat
        id — and every item reports it. Concatenating them produced a detail
        string repeated once per item (31 identical "timed out" clauses in the
        Phase 3 smoke run), which buries the actual reason in noise. Distinct
        reasons are kept; duplicates are dropped.
        """
        if detail and detail not in self._details:
            self._details.append(detail)

    @property
    def attempted(self) -> int:
        return len(self.delivered_keys) + len(self.failed_keys) + len(self.skipped_keys)

    @property
    def delivered(self) -> int:
        return len(self.delivered_keys)

    @property
    def failed(self) -> int:
        return len(self.failed_keys)

    @property
    def skipped(self) -> int:
        return len(self.skipped_keys)

    @property
    def ok(self) -> bool:
        return not self.failed_keys

    def record(self, identity_key: str, *, ok: bool, detail: str = "") -> None:
        """Record one item's outcome — the one way adapters should report.

        Keeps the key lists and the derived counts in step by construction, so
        an adapter cannot report ``delivered=1`` while listing no key (which
        would leave the item pending forever) or the reverse.
        """
        if ok:
            self.delivered_keys.append(identity_key)
        else:
            self.failed_keys.append(identity_key)
        self._add_detail(detail)

    def skip(self, identity_key: str, *, detail: str = "") -> None:
        """Record one item the adapter declined to handle.

        Not a failure and not a delivery: the caller must leave it neither
        delivered nor retried-forever, which is why it gets its own bucket
        rather than being folded into either of the other two.
        """
        self.skipped_keys.append(identity_key)
        self._add_detail(detail)

    def to_dict(self) -> dict:
        return {
            "output": self.output,
            "attempted": self.attempted,
            "delivered": self.delivered,
            "failed": self.failed,
            "skipped": self.skipped,
            "delivered_keys": list(self.delivered_keys),
            "failed_keys": list(self.failed_keys),
            "skipped_keys": list(self.skipped_keys),
            "detail": self.detail,
        }


def record_batch_failure(
    result: DeliveryResult, items: list[NormalizedItem], detail: str
) -> None:
    """Mark every item in ``items`` as failed with one shared reason.

    Used by the two paths where an adapter knows nothing was sent: credentials
    missing, and a batch aborted after repeated failures. In both cases the items
    must stay pending, and writing the same reason once per item through
    :meth:`DeliveryResult.record` keeps the key lists and the counts in step.
    """
    for item in items:
        result.record(item.identity_key, ok=False, detail=detail)


class OutputAdapter(ABC):
    """Sends or publishes normalized items somewhere."""

    #: Stable identifier. Also the delivery-state namespace in storage, so two
    #: adapters can never share (or overwrite) each other's delivery records.
    name: str = "base"

    @abstractmethod
    def emit(self, items: list[NormalizedItem]) -> DeliveryResult:
        """Deliver ``items``.

        Implementations must be tolerant: a failure to deliver one item is
        reported in the result, never raised in a way that would discard the
        rest. Items are read-only inputs — mutating them would corrupt the
        stored record for every other adapter.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release adapter-owned resources. Never raises."""

    def __enter__(self) -> OutputAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
