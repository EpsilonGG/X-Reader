"""The run loop.

This is the whole data flow, and nothing else::

    Unit -> Provider/Fetcher -> Raw Response -> Parser -> Raw record
                                                              |
                                                              v
                                                        Normalizer
                                                              |
                                                              v
                                                       NormalizedItem
                                                              |
                                                              v
                                                           Storage
                                                              |
                                                              v
                                                     pending_for(output)
                                                              |
                                                              v
                                                    OutputAdapter.emit()
                                                              |
                                                              v
                                                      mark_delivered()

A **unit** is one tracked thing: an X account, an RSS source, or a YouTube
channel. All three go through the same pipeline; only the parser and the
normalizer kind differ, and the binding between them lives in
``app/registry.py``.

Acquisition and delivery
------------------------
Delivery runs **once, after every unit has been processed** (task section 14).
That ordering is not an optimisation: a run's output is the run, not each unit,
and delivering per unit would send the same digest several times.

The runner orchestrates and nothing more. It does not know what Telegram is, and
it does not know what QQ is: it asks storage which keys are pending for an
output, reads those items back, hands them to the adapter, and records what the
adapter reports. Every rule about Telegram — the API, the 4096-character limit,
HTML escaping — lives in ``outputs/``, and every rule about OneBot lives in
``outputs/qq.py``. There is no ``httpx`` call and no ``api.telegram.org`` or
``/send_group_msg`` string in this file (task section 13).

The outputs are independent by construction: the loop below asks each adapter in
turn and writes each one's result into its own delivery namespace, so "Telegram
sent, QQ failed" is two files, not one compromised state.

Delivery is **at-least-once**. State is written after ``emit()`` returns, so a
crash between sending and recording re-sends next run. The opposite order would
mark an item delivered that never arrived, and losing content is the one failure
the freeze does not tolerate.

What this module deliberately does *not* do
-------------------------------------------
No RSS rendering, no feeds, no GitHub Pages, no summary, no LLM, no scheduling,
and no knowledge of any delivery protocol. It drives adapters through
``OutputAdapter`` and never learns whether one speaks Telegram or OneBot.

Failure model (freeze v2, section 17)
-------------------------------------
Errors are handled at the narrowest scope that can still make progress:

* one **route** failing moves on to the next route;
* one **unit** failing is recorded and the loop continues to the next unit — a
  dead account or a dead feed can never take down a healthy one;
* **storage** failing is the one error that aborts the remaining routes for that
  unit, because every route writes to the same place; retrying a different URL
  cannot fix a broken disk;
* **audit, raw-capture and raw-archive** failures are downgraded to warnings.
  Losing a log line or an evidence copy must never lose fetched data.

Every failure carries its ``kind`` into the run record, so an operator can tell
a network problem from a parser problem without reading stack traces.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from uuid import uuid4

from app.registry import normalizer_kind_for
from app.registry import parser_for
from app.registry import unbound_routes
from config.schema import Config
from config.schema import RssSourceConfig
from config.schema import YoutubeChannelConfig
from domain.errors import AllProvidersFailed
from domain.errors import StorageError
from domain.errors import XReaderError
from domain.models.account import Account
from domain.models.item import NormalizedItem
from domain.models.item import SOURCE_YOUTUBE
from domain.models.tweet import RawTweet
from infrastructure.http.response import RawResponse
from normalizers.registry import normalizer_for
from outputs.base import OutputAdapter
from providers.base import BaseProvider
from storage.base import DELIVERY_FAILED
from storage.base import DELIVERY_SENT
from storage.base import DELIVERY_SKIPPED
from storage.base import BaseStorage
from storage.base import ItemReader
from storage.base import RawRecordArchive
from storage.base import RunRecord

logger = logging.getLogger("x_reader.runner")

STATUS_OK = "ok"
STATUS_NO_TWEETS = "no_tweets"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"

KIND_X = "x"
KIND_RSS = "rss"
KIND_YOUTUBE = "youtube"


def _utcnow() -> datetime:
    # datetime.utcnow() is deprecated in 3.12 and returns a naive value; X-rss
    # still uses it. Aware timestamps are used here instead.
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _ms(started: datetime, finished: datetime) -> int:
    return max(0, int((finished - started).total_seconds() * 1000))


@dataclass(slots=True)
class AccountOutcome:
    """What happened to one tracked unit in one run.

    ``account`` is the unit's label — an X handle, or an RSS source id. The name
    is Phase 1's; ``kind`` says which sort of unit it is.
    """

    account: str
    status: str
    kind: str = KIND_X
    source_id: str = ""
    provider: str = ""
    route: str = ""
    fetched: int = 0
    normalized: int = 0
    inserted: int = 0
    duplicates: int = 0
    invalid: int = 0
    error_kind: str | None = None
    error: str | None = None
    attempts: list[dict] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def healthy(self) -> bool:
        """True when nothing needs attention — including a genuinely empty feed."""
        return self.status in (STATUS_OK, STATUS_NO_TWEETS)

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "status": self.status,
            "kind": self.kind,
            "source_id": self.source_id,
            "provider": self.provider,
            "route": self.route,
            "fetched": self.fetched,
            "normalized": self.normalized,
            "inserted": self.inserted,
            "duplicates": self.duplicates,
            "invalid": self.invalid,
            "error_kind": self.error_kind,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
        }


@dataclass(slots=True)
class DeliveryOutcome:
    """What one output adapter did in one run.

    Kept separate from :class:`AccountOutcome` because it answers a different
    question — "was this published?" rather than "was this fetched?" — and
    merging them would let a delivery problem look like an acquisition problem.
    """

    output: str
    #: Items that were pending for this output when the pass started.
    pending: int = 0
    delivered: int = 0
    failed: int = 0
    skipped: int = 0
    #: Why it went wrong, if it did. Never contains a credential.
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "output": self.output,
            "pending": self.pending,
            "delivered": self.delivered,
            "failed": self.failed,
            "skipped": self.skipped,
            "detail": self.detail,
        }


@dataclass(slots=True)
class RunSummary:
    """The result of a whole run — what ``main.py`` prints and returns."""

    run_id: str
    started_at: str
    finished_at: str = ""
    duration_ms: int = 0
    outcomes: list[AccountOutcome] = field(default_factory=list)
    #: One entry per configured output adapter; empty when none is configured.
    deliveries: list[DeliveryOutcome] = field(default_factory=list)

    # -- aggregates ---------------------------------------------------------
    @property
    def fetched(self) -> int:
        return sum(o.fetched for o in self.outcomes)

    @property
    def normalized(self) -> int:
        return sum(o.normalized for o in self.outcomes)

    @property
    def inserted(self) -> int:
        return sum(o.inserted for o in self.outcomes)

    @property
    def duplicates(self) -> int:
        return sum(o.duplicates for o in self.outcomes)

    def _count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def accounts_ok(self) -> int:
        return self._count(STATUS_OK)

    @property
    def accounts_empty(self) -> int:
        return self._count(STATUS_NO_TWEETS)

    @property
    def accounts_failed(self) -> int:
        return self._count(STATUS_ERROR)

    @property
    def accounts_skipped(self) -> int:
        return self._count(STATUS_SKIPPED)

    @property
    def attempted(self) -> int:
        """Units actually attempted (not skipped by configuration)."""
        return sum(1 for o in self.outcomes if o.status != STATUS_SKIPPED)

    # -- delivery aggregates -------------------------------------------------
    @property
    def delivered(self) -> int:
        return sum(d.delivered for d in self.deliveries)

    @property
    def delivery_failed(self) -> int:
        return sum(d.failed for d in self.deliveries)

    @property
    def deliveries_ok(self) -> bool:
        """True when no output reported a failure. Vacuously true if none ran."""
        return all(d.ok for d in self.deliveries)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "accounts_total": len(self.outcomes),
            "accounts_ok": self.accounts_ok,
            "accounts_empty": self.accounts_empty,
            "accounts_failed": self.accounts_failed,
            "accounts_skipped": self.accounts_skipped,
            "tweets_fetched": self.fetched,
            "records_normalized": self.normalized,
            "tweets_inserted": self.inserted,
            "tweets_duplicate": self.duplicates,
            "delivered": self.delivered,
            "delivery_failed": self.delivery_failed,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "deliveries": [d.to_dict() for d in self.deliveries],
        }


class Runner:
    """Compose providers, parsers, normalizers and storage into one pass."""

    def __init__(
        self,
        *,
        config: Config,
        accounts: list[Account],
        providers: list[BaseProvider],
        storage: BaseStorage,
        archive: RawRecordArchive | None = None,
        rss_sources: list[RssSourceConfig] | None = None,
        rss_provider: BaseProvider | None = None,
        youtube_channels: list[YoutubeChannelConfig] | None = None,
        youtube_provider: BaseProvider | None = None,
        outputs: list[OutputAdapter] | None = None,
        reader: ItemReader | None = None,
        run_id: str | None = None,
    ) -> None:
        self.config = config
        self.accounts = accounts
        self.providers = providers
        self.storage = storage
        #: Optional: the raw-record archive is evidence, not the data flow.
        self.archive = archive
        self.rss_sources = list(rss_sources or [])
        self.rss_provider = rss_provider
        self.youtube_channels = list(youtube_channels or [])
        self.youtube_provider = youtube_provider
        #: Optional downstream consumers. Empty means "acquire and stop", which
        #: is a complete, supported configuration — not a degraded one.
        self.outputs = list(outputs or [])
        #: Where delivery reads items back from. Separate from ``storage``
        #: because reading items is a downstream need, not part of the
        #: acquisition contract (see ``storage.base.ItemReader``).
        self.reader = reader
        self.run_id = run_id or self._new_run_id()
        self.limit = config.fetch.limit
        #: Wall-clock origin shared by every run record of this run.
        self.started_at = _iso(_utcnow())

    @staticmethod
    def _new_run_id() -> str:
        return f"{_utcnow():%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"

    # -- entry point --------------------------------------------------------
    def run(self) -> RunSummary:
        started = _utcnow()
        self.started_at = _iso(started)
        summary = RunSummary(run_id=self.run_id, started_at=self.started_at)

        live: list[BaseProvider] = list(self.providers)
        if self.rss_provider is not None:
            live.append(self.rss_provider)
        if self.youtube_provider is not None:
            live.append(self.youtube_provider)

        missing = unbound_routes(live)
        if missing:
            # A wiring bug, reported once and loudly, but not fatal: the routes
            # that *are* bound can still produce data.
            logger.error("no parser bound for route(s): %s", ", ".join(missing))
        if not live:
            logger.error("no providers available; nothing can be fetched")

        for account in self.accounts:
            if not account.enabled:
                logger.info("@%s: skipped (disabled in accounts.yaml)", account.username)
                summary.outcomes.append(
                    AccountOutcome(account=account.username, status=STATUS_SKIPPED)
                )
                continue
            summary.outcomes.append(
                self._process(
                    account,
                    self.providers,
                    kind=KIND_X,
                    label=account.username,
                    display=f"@{account.username}",
                )
            )

        for source in self.rss_sources:
            if not source.enabled:
                logger.info("%s: skipped (disabled in config)", source.id)
                summary.outcomes.append(
                    AccountOutcome(account=source.id, status=STATUS_SKIPPED, kind=KIND_RSS)
                )
                continue
            # An RSS source is passed through the tracked-unit slot with its
            # config id as the identifier — see providers/rss.py.
            unit = Account(username=source.id)
            summary.outcomes.append(
                self._process(
                    unit,
                    [self.rss_provider] if self.rss_provider else [],
                    kind=KIND_RSS,
                    label=source.id,
                    display=f"rss:{source.id}",
                    source_id=source.id,
                )
            )

        for channel in self.youtube_channels:
            if not channel.enabled:
                logger.info("%s: skipped (disabled in config)", channel.channel_id)
                summary.outcomes.append(
                    AccountOutcome(
                        account=channel.channel_id,
                        status=STATUS_SKIPPED,
                        kind=KIND_YOUTUBE,
                    )
                )
                continue
            # A YouTube channel takes the same tracked-unit slot, with its
            # channel_id as the identifier — see providers/youtube.py. The
            # reported source_id matches the namespace the normalizer stores
            # under, so the run record and the stored items agree.
            unit = Account(username=channel.channel_id)
            summary.outcomes.append(
                self._process(
                    unit,
                    [self.youtube_provider] if self.youtube_provider else [],
                    kind=KIND_YOUTUBE,
                    label=channel.channel_id,
                    display=f"youtube:{channel.channel_id}",
                    source_id=f"{SOURCE_YOUTUBE}:{channel.channel_id}",
                )
            )

        # --- deliver --------------------------------------------------------
        # After acquisition, never during it: one pass covers the whole run, and
        # an output problem can never influence what gets fetched or stored.
        summary.deliveries = self._deliver()

        finished = _utcnow()
        summary.finished_at = _iso(finished)
        summary.duration_ms = _ms(started, finished)
        return summary

    # -- delivery -----------------------------------------------------------
    def _deliver(self) -> list[DeliveryOutcome]:
        return [self._deliver_one(output) for output in self.outputs]

    def _deliver_one(self, output: OutputAdapter) -> DeliveryOutcome:
        """One output's pass. Never raises: delivery cannot fail the run."""
        outcome = DeliveryOutcome(output=output.name)

        try:
            pending = self.storage.pending_for(output.name, sorted(self.storage.known_keys()))
        except StorageError as exc:
            logger.error("delivery[%s]: cannot read delivery state: %s", output.name, exc)
            outcome.detail = str(exc)
            return outcome

        outcome.pending = len(pending)
        if not pending:
            logger.info("delivery[%s]: nothing pending", output.name)
            return outcome

        items = self._items_for_delivery(pending)
        if items is None:
            outcome.detail = "storage cannot read items back; nothing was delivered"
            logger.error("delivery[%s]: %s", output.name, outcome.detail)
            return outcome

        try:
            result = output.emit(items)
        except Exception as exc:  # noqa: BLE001 - an adapter bug is not a run failure
            # An adapter that raises has told us nothing about what it sent, so
            # every pending item is recorded as failed and therefore stays
            # pending. Assuming success here would silently lose content.
            logger.exception("delivery[%s]: adapter raised", output.name)
            result = None
            outcome.failed = len(pending)
            outcome.detail = repr(exc)
            self._record_delivery(output.name, [], pending, [], outcome.detail)
            return outcome

        self._persist_delivery(output, result, outcome)
        if result.ok:
            logger.info(
                "delivery[%s]: %d delivered, %d skipped",
                output.name, result.delivered, result.skipped,
            )
        else:
            logger.error(
                "delivery[%s]: %d delivered, %d failed, %d skipped — %s",
                output.name, result.delivered, result.failed, result.skipped,
                result.detail,
            )
        return outcome

    def _items_for_delivery(self, pending: list[str]) -> list[NormalizedItem] | None:
        """Read the pending items back out of storage.

        Delivery state is a set of keys, but an adapter needs the items — and a
        failed delivery is retried on a *later* run, when the item is long gone
        from memory. So the read-back is the only way to retry without a
        refetch, which is exactly the property the freeze requires.
        """
        if self.reader is None:
            return None
        wanted = set(pending)
        items = [item for item in self.reader.load_items() if item.identity_key in wanted]
        # Deterministic order matching ``pending`` (which is sorted by key), so
        # two runs of the same data deliver in the same order.
        order = {key: index for index, key in enumerate(pending)}
        items.sort(key=lambda item: order.get(item.identity_key, len(order)))
        return items

    def _persist_delivery(
        self, output: OutputAdapter, result, outcome: DeliveryOutcome
    ) -> None:
        """Record what the adapter reported, then report it upward."""
        outcome.delivered = result.delivered
        outcome.failed = result.failed
        outcome.skipped = result.skipped
        outcome.detail = result.detail
        self._record_delivery(
            output.name,
            result.delivered_keys,
            result.failed_keys,
            result.skipped_keys,
            result.detail,
        )

    def _record_delivery(
        self,
        output: str,
        delivered: list[str],
        failed: list[str],
        skipped: list[str],
        detail: str,
    ) -> None:
        """Write delivery state. Failing here leaves items pending, by design.

        Written *after* the send. The cost is a possible duplicate message if
        this write fails; the alternative — marking first — risks recording as
        delivered something that never arrived. Duplicates are recoverable,
        lost content is not.
        """
        try:
            if delivered:
                self.storage.mark_delivered(output, delivered, DELIVERY_SENT)
            if failed:
                self.storage.mark_delivered(output, failed, DELIVERY_FAILED, detail)
            if skipped:
                self.storage.mark_delivered(output, skipped, DELIVERY_SKIPPED, detail)
        except StorageError as exc:
            logger.error("delivery[%s]: cannot record delivery state: %s", output, exc)

    # -- one unit -----------------------------------------------------------
    def _process(
        self,
        unit: Account,
        providers: list[BaseProvider],
        *,
        kind: str,
        label: str,
        display: str,
        source_id: str = "",
    ) -> AccountOutcome:
        started = _utcnow()
        outcome = AccountOutcome(
            account=label, status=STATUS_ERROR, kind=kind, source_id=source_id
        )
        causes: dict[str, str] = {}

        # Remembers a route that fetched successfully but yielded no records.
        # If every route either fails or comes back empty, an empty success is
        # the honest verdict — the unit simply has nothing new — and must not be
        # reported as a failure (freeze: a page with no items is normal).
        empty_success: tuple[str, str] | None = None

        for provider in providers:
            for route in provider.routes():
                label_route = f"{provider.name}/{route}"
                attempt_started = _utcnow()
                attempt: dict = {
                    "provider": provider.name,
                    "route": route,
                    "status": "error",
                    "error_kind": None,
                    "error": None,
                    "fetched": 0,
                    "normalized": 0,
                    "inserted": 0,
                    "url": "",
                    "duration_ms": 0,
                }

                parser = parser_for(provider.name, route)
                if parser is None:
                    # Unbound route: a wiring bug, not an upstream failure.
                    message = f"no parser bound for {label_route}"
                    attempt["error_kind"] = "configuration_error"
                    attempt["error"] = message
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    causes[label_route] = message
                    logger.error("%s: %s", display, message)
                    continue

                normalizer_kind = normalizer_kind_for(provider.name, route) or kind
                normalizer = normalizer_for(normalizer_kind)
                if normalizer is None:
                    message = f"no normalizer bound for kind '{normalizer_kind}'"
                    attempt["error_kind"] = "configuration_error"
                    attempt["error"] = message
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    causes[label_route] = message
                    logger.error("%s: %s", display, message)
                    continue

                # --- fetch --------------------------------------------------
                try:
                    response = provider.fetch(unit, route)
                except XReaderError as exc:
                    attempt["error_kind"] = exc.kind
                    attempt["error"] = str(exc)
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    causes[label_route] = f"{exc.kind}: {exc}"
                    logger.warning("%s: %s failed: %s", display, label_route, exc)
                    continue
                except Exception as exc:  # noqa: BLE001 - last line of defence
                    # A provider bug must not abort the run for other units.
                    attempt["error_kind"] = "unexpected_error"
                    attempt["error"] = repr(exc)
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    causes[label_route] = f"unexpected_error: {exc!r}"
                    logger.exception("%s: %s raised unexpectedly", display, label_route)
                    continue

                attempt["url"] = response.url

                # --- parse --------------------------------------------------
                records: list = []
                try:
                    records = parser.parse(response, unit)
                except XReaderError as exc:
                    attempt["error_kind"] = exc.kind
                    attempt["error"] = str(exc)
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    causes[label_route] = f"{exc.kind}: {exc}"
                    logger.warning("%s: %s parse failed: %s", display, label_route, exc)
                    self._capture(unit, provider.name, route, response, parsed=0)
                    continue
                except Exception as exc:  # noqa: BLE001
                    attempt["error_kind"] = "unexpected_error"
                    attempt["error"] = repr(exc)
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    causes[label_route] = f"unexpected_error: {exc!r}"
                    logger.exception("%s: %s parser raised unexpectedly", display, label_route)
                    self._capture(unit, provider.name, route, response, parsed=0)
                    continue

                # `limit` caps how much a single X unit can contribute to one run.
                # Nitter serves roughly one page per request and returns
                # newest-first, so in practice this is a safety cap rather than a
                # window — real pagination is a later-phase concern, and the raw
                # bodies are retained so nothing is lost irrecoverably.
                #
                # It is deliberately NOT applied to RSS. A feed arrives as one
                # complete document, so there is no pagination to protect against
                # and a cap can only discard real content — and with the default
                # `on_error` capture policy nothing would be kept as evidence
                # either, so the discarded items would be unreachable until the
                # feed happened to rotate them back into the window. The real
                # reference feed in this workspace has 24 items, which the default
                # limit of 20 would silently truncate.
                if kind == KIND_X and self.limit and len(records) > self.limit:
                    logger.debug(
                        "%s: %s returned %d records, capped to %d",
                        display, label_route, len(records), self.limit,
                    )
                    records = records[: self.limit]

                self._capture(
                    unit, provider.name, route, response, parsed=len(records)
                )

                if not records:
                    attempt["status"] = "empty"
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    empty_success = empty_success or (provider.name, route)
                    logger.info("%s: %s returned no records", display, label_route)
                    continue

                # --- stamp provenance ---------------------------------------
                # Parsers are pure readers of bytes and cannot know which
                # provider produced them (and the same parser serves two
                # providers). The runner owns provenance, so it stamps it here
                # rather than letting a parser guess.
                for record in records:
                    if isinstance(record, RawTweet):
                        record.provider = provider.name
                        record.route = route
                        if not record.fetched_at:
                            record.fetched_at = response.fetched_at.isoformat()

                # --- normalize ----------------------------------------------
                # The only step that knows both a source's shape and the shared
                # contract. Pure: no I/O, no storage, no formatting.
                try:
                    items: list[NormalizedItem] = normalizer.normalize(records)
                except Exception as exc:  # noqa: BLE001 - a normalizer bug is ours
                    attempt["error_kind"] = "normalizer_error"
                    attempt["error"] = repr(exc)
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    causes[label_route] = f"normalizer_error: {exc!r}"
                    logger.exception("%s: %s normalizer raised", display, label_route)
                    continue

                attempt["fetched"] = len(records)
                attempt["normalized"] = len(items)

                if not items:
                    # Records arrived but none satisfied the contract. Treat it
                    # like an empty page: try the next route, do not claim ok.
                    attempt["status"] = "empty"
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    empty_success = empty_success or (provider.name, route)
                    logger.warning(
                        "%s: %s produced %d records but 0 valid items",
                        display, label_route, len(records),
                    )
                    continue

                # --- archive raw evidence (never fatal) ---------------------
                self._archive_raw(unit, records, display=display)

                # --- store --------------------------------------------------
                try:
                    report = self.storage.save(items)
                except StorageError as exc:
                    # Every route writes to the same place; trying another URL
                    # cannot repair storage. Stop this unit, keep the run.
                    attempt["error_kind"] = exc.kind
                    attempt["error"] = str(exc)
                    attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                    outcome.attempts.append(attempt)
                    causes[label_route] = f"{exc.kind}: {exc}"
                    logger.error("%s: storage failed: %s", display, exc)
                    break

                attempt["status"] = "ok"
                attempt["inserted"] = report.inserted
                attempt["duration_ms"] = _ms(attempt_started, _utcnow())
                outcome.attempts.append(attempt)

                outcome.status = STATUS_OK
                outcome.provider = provider.name
                outcome.route = route
                outcome.fetched = report.received
                outcome.normalized = len(items)
                outcome.inserted = report.inserted
                outcome.duplicates = report.duplicates
                outcome.invalid = report.invalid
                outcome.duration_ms = _ms(started, _utcnow())

                logger.info(
                    "%s: %s ok — %d fetched, %d normalized, %d new, %d duplicate",
                    display, label_route, report.received, len(items),
                    report.inserted, report.duplicates,
                )
                self._record_run(unit, outcome, display=display)
                return outcome

        # --- every route is exhausted -------------------------------------
        if empty_success is not None:
            provider_name, route = empty_success
            outcome.status = STATUS_NO_TWEETS
            outcome.provider = provider_name
            outcome.route = route
            logger.info("%s: no records from any route", display)
        else:
            failure = AllProvidersFailed(label, causes)
            outcome.status = STATUS_ERROR
            outcome.error_kind = failure.kind
            outcome.error = str(failure)
            logger.error("%s", failure)

        outcome.duration_ms = _ms(started, _utcnow())
        self._record_run(unit, outcome, display=display)
        return outcome

    # -- side channels (never fatal) ---------------------------------------
    def _capture(
        self,
        unit: Account,
        provider: str,
        route: str,
        response: RawResponse,
        *,
        parsed: int,
    ) -> None:
        """Keep the raw body when policy says so. Failure is only a warning."""
        should = getattr(self.storage, "should_capture", None)
        if callable(should) and not should(failed=False, parsed=parsed):
            return
        try:
            path = self.storage.capture_raw(unit, provider, route, response)
        except StorageError as exc:
            logger.warning("%s: raw capture failed: %s", unit.username, exc)
            return
        if path:
            logger.debug("%s: raw response saved to %s", unit.username, path)

    def _archive_raw(self, unit: Account, records: list, *, display: str) -> None:
        """Archive raw X tweets as re-processing evidence.

        Only X has a raw record archive: its shape was frozen in Phase 1, so it
        is stable enough to keep. RSS and YouTube records have no such archive
        yet, and inventing one before a real sample is in play would freeze the
        wrong shape. Re-parseability for both is provided by response-body
        capture instead.

        Never fatal: losing an evidence copy must not lose fetched data.
        """
        if self.archive is None:
            return
        tweets = [record for record in records if isinstance(record, RawTweet)]
        if not tweets:
            return
        try:
            self.archive.archive_raw_tweets(unit, tweets)
        except StorageError as exc:
            logger.warning("%s: raw archive failed: %s", display, exc)

    def _record_run(self, unit: Account, outcome: AccountOutcome, *, display: str) -> None:
        """Append the audit line. A logging failure must never fail a run."""
        record = RunRecord(
            run_id=self.run_id,
            account=outcome.account,
            status=outcome.status,
            kind=outcome.kind,
            source_id=outcome.source_id,
            provider=outcome.provider,
            route=outcome.route,
            fetched=outcome.fetched,
            normalized=outcome.normalized,
            inserted=outcome.inserted,
            duplicates=outcome.duplicates,
            error_kind=outcome.error_kind,
            error=outcome.error,
            started_at=self.started_at,
            finished_at=_iso(_utcnow()),
            duration_ms=outcome.duration_ms,
            attempts=outcome.attempts,
        )
        try:
            self.storage.record_run(record)
        except StorageError as exc:
            logger.warning("%s: could not write run record: %s", display, exc)
