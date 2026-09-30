"""JSONL storage backend.

Storage technology choice — and why
====================================

The freeze (v2 section 13, v3 section 11.2) asks for a real decision, weighed
against GitHub Actions, repository storage, data volume, concurrency,
incremental updates, git diff quality and maintainability. Both candidates were
considered.

**SQLite** (what x-tweet-fetcher's ledger uses) is the stronger *database*:
``tweet_id`` as a primary key makes deduplication a hard constraint rather than
application logic, it gives ACID semantics, and it answers future queries
(``--query``, ``--stats``) without loading everything into memory. Its fatal
problem here is where the data has to live. The only durable persistence
available to a free GitHub Actions run is the repository itself, so the database
file must be committed. A binary blob is re-committed in full on every run:
git cannot diff it, cannot delta-compress it meaningfully, and cannot merge it.
A ~50 MB database committed daily for a year turns ``.git`` into gigabytes, and
a corrupt blob is unrecoverable — there is no textual history to fall back on.

**JSONL** (chosen) keeps every record as one line of text:

* **Append-only means tiny diffs.** A run that adds 30 items adds 30 lines.
  Git delta-compresses appends extremely well, so repository growth tracks
  *actual new data*, not the total dataset size.
* **Fail-safe, not fail-fatal.** A truncated or corrupt line is skipped and
  counted; the remaining records stay readable. A corrupt binary database is
  a total loss. For a store whose whole purpose is "re-processable later",
  recoverability beats query convenience.
* **Reviewable.** ``git log -p data/items/x.jsonl`` shows exactly which items a
  run collected. That is real operational value.
* **Re-processable.** The raw archive keeps the parser's original extraction, so
  a future Normalizer can re-derive a better model with no refetch.
* **Incremental by construction.** Only keys not already present are appended, so
  re-running produces zero new lines (verified by tests).

Costs accepted, explicitly:

* Deduplication is enforced by the application, not the engine. Mitigated by
  keeping the dedup decision in one small, unit-tested function.
* ``known_keys()`` reads whole files. A JSON parse per line is fast (~tens of
  thousands of lines per second) and memory stays at O(keys), but it is linear.
  That is the one real scalability limit, and it is exactly what the freeze
  schedules for Phase 3, where a derived SQLite index can be added *behind the
  same ``BaseStorage`` interface* without disturbing any caller.

Layout::

    data/
      items/<source_id>.jsonl        canonical NormalizedItem records
      accounts/<unit>.jsonl          raw tweet archive (Phase 1 evidence)
      runs/<YYYY-MM-DD>.jsonl        append-only audit trail
      raw/<unit>/<ts>__<provider>__<route>.<ext>   optional raw bodies
      delivery/<output>.jsonl        per-output delivery state

``accounts/`` keeps its Phase 1 name because renaming a directory that holds
committed history would be a data migration for cosmetic gain. It is the
per-unit **raw record archive**: today only X writes to it, and it is not part
of the Phase 2 data flow.

Semantics: **first write wins**. An existing identity is skipped, never
rewritten, so records stay immutable and the append-only guarantee holds.
Enrichment of an already-stored item (for example when a richer route succeeds
after a poorer one) is a Phase 3 concern; see ``docs/NEXT_PHASE_PLAN.md``.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from datetime import timezone
from pathlib import Path

from domain.errors import StorageError
from domain.models.account import Account
from domain.models.item import NormalizedItem
from domain.models.item import build_identity_key
from domain.models.tweet import RawTweet
from infrastructure.http.response import RawResponse
from storage.base import DELIVERY_SENT
from storage.base import BaseStorage
from storage.base import DeliveryRecord
from storage.base import ItemReader
from storage.base import RawRecordArchive
from storage.base import RunRecord
from storage.base import SaveReport

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")

_EXTENSIONS = {
    "text/html": ".html",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/rss+xml": ".xml",
    "application/atom+xml": ".xml",
    "application/json": ".json",
}


def _safe_name(value: str) -> str:
    """Filesystem-safe component (X handles are already ``[A-Za-z0-9_]``)."""
    cleaned = _SAFE_NAME.sub("_", (value or "").strip())
    return cleaned or "unknown"


def _unit_name(unit: Account | str) -> str:
    """The identifier of a tracked unit: an X handle, or a source id."""
    if isinstance(unit, Account):
        return unit.username
    return str(unit)


def _read_jsonl(path: Path) -> tuple[list[dict], int]:
    """Stream a JSONL file. Returns ``(records, malformed_count)``.

    A line that is not valid JSON is skipped and counted rather than raising:
    a single torn line (process killed mid-append) must not make the rest of the
    file unreadable.
    """
    records: list[dict] = []
    malformed = 0
    if not path.exists():
        return records, malformed
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(record, dict):
                    malformed += 1
                    continue
                records.append(record)
    except OSError as exc:
        raise StorageError(f"cannot read {path}: {exc}") from exc
    return records, malformed


def _append_jsonl(path: Path, payloads: list[dict]) -> None:
    """Append ``payloads`` as one JSON object per line."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for payload in payloads:
                handle.write(json.dumps(payload, ensure_ascii=False))
                handle.write("\n")
            handle.flush()
    except OSError as exc:
        raise StorageError(f"cannot write {path}: {exc}") from exc


class JsonlStorage(BaseStorage, RawRecordArchive, ItemReader):
    """Append-only JSONL storage: normalized items, raw archive, runs, delivery."""

    def __init__(self, data_dir: str | Path = "data", raw_policy: str = "on_error") -> None:
        self.data_dir = Path(data_dir)
        self.items_dir = self.data_dir / "items"
        self.accounts_dir = self.data_dir / "accounts"
        self.runs_dir = self.data_dir / "runs"
        self.raw_dir = self.data_dir / "raw"
        self.delivery_dir = self.data_dir / "delivery"
        self.raw_policy = raw_policy

    @classmethod
    def from_config(cls, config) -> JsonlStorage:  # noqa: ANN001 - config.schema.StorageConfig
        return cls(data_dir=config.data_path, raw_policy=config.store_raw_response)

    # -- paths --------------------------------------------------------------
    def item_path(self, source_id: str) -> Path:
        return self.items_dir / f"{_safe_name(source_id)}.jsonl"

    def account_path(self, unit: Account | str) -> Path:
        return self.accounts_dir / f"{_safe_name(_unit_name(unit))}.jsonl"

    def delivery_path(self, output: str) -> Path:
        return self.delivery_dir / f"{_safe_name(output)}.jsonl"

    # -- canonical: NormalizedItem -----------------------------------------
    def known_keys(self, source_id: str | None = None) -> set[str]:
        if source_id is not None:
            return self._scan_items(self.item_path(source_id))[0]
        keys: set[str] = set()
        if self.items_dir.exists():
            for path in sorted(self.items_dir.glob("*.jsonl")):
                keys |= self._scan_items(path)[0]
        return keys

    @staticmethod
    def _scan_items(path: Path) -> tuple[set[str], int]:
        records, malformed = _read_jsonl(path)
        keys: set[str] = set()
        for record in records:
            key = record.get("identity_key")
            if not key:
                # Tolerate a line written before the derived property existed:
                # derive it rather than losing the record's identity.
                source = record.get("source_id")
                item_id = record.get("item_id")
                if source and item_id:
                    key = build_identity_key(str(source), str(item_id))
            if key:
                keys.add(str(key))
        return keys, malformed

    def save(self, items: list[NormalizedItem]) -> SaveReport:
        report = SaveReport(received=len(items), path=str(self.items_dir))
        if not items:
            return report

        # Group by source so each source keeps its own file — and so two sources
        # that reuse the same item_id can never collide.
        grouped: dict[str, list[NormalizedItem]] = {}
        for item in items:
            grouped.setdefault(item.source_id, []).append(item)
        if len(grouped) == 1:
            only = next(iter(grouped))
            report.source_id = only
            report.path = str(self.item_path(only))

        for source_id, batch in grouped.items():
            path = self.item_path(source_id)
            known, malformed = self._scan_items(path)
            report.malformed_lines += malformed

            fresh: list[NormalizedItem] = []
            for item in batch:
                if not item.is_valid():
                    # No identity, or no content at all. Storage refuses to guess.
                    report.invalid += 1
                    continue
                key = item.identity_key
                if key in known:
                    report.duplicates += 1
                    continue
                known.add(key)
                fresh.append(item)

            if fresh:
                _append_jsonl(path, [item.to_dict() for item in fresh])
                report.inserted += len(fresh)

        return report

    # -- read-back for output adapters --------------------------------------
    def load_items(self, source_id: str | None = None) -> list[NormalizedItem]:
        """Rehydrate stored items, for delivery on a later run.

        Phase 3 needs this because a failed delivery is retried on the *next*
        run, when the item is long out of memory — ``pending_for()`` can say
        "this key is undelivered" but the adapter needs the content too.

        Malformed lines are skipped and counted rather than raising, exactly as
        in the dedup scan: one torn line must not make the rest unreadable. A
        line whose ``identity_key`` disagrees with its components is not
        silently trusted — ``from_dict`` re-derives it (see DR-3).
        """
        paths = (
            [self.item_path(source_id)]
            if source_id is not None
            else sorted(self.items_dir.glob("*.jsonl")) if self.items_dir.exists() else []
        )

        items: list[NormalizedItem] = []
        for path in paths:
            records, _ = _read_jsonl(path)
            for record in records:
                item = NormalizedItem.from_dict(record)
                # A stored line that lost its identity cannot be deduplicated or
                # addressed for delivery, so it is dropped here rather than
                # being handed downstream as an unaddressable item.
                if item.is_valid():
                    items.append(item)
        return items

    # -- raw record archive (evidence, not the canonical store) -------------
    def known_ids(self, unit: Account | str) -> set[str]:
        records, _ = _read_jsonl(self.account_path(unit))
        ids: set[str] = set()
        for record in records:
            tweet_id = record.get("tweet_id")
            if tweet_id:
                ids.add(str(tweet_id))
        return ids

    def archive_raw_tweets(self, unit: Account | str, tweets: list[RawTweet]) -> SaveReport:
        report = SaveReport(received=len(tweets), unit=_unit_name(unit))
        path = self.account_path(unit)
        report.path = str(path)
        if not tweets:
            return report

        known, malformed = _read_jsonl(path)
        report.malformed_lines = malformed
        seen = {str(r.get("tweet_id")) for r in known if r.get("tweet_id")}

        fresh: list[RawTweet] = []
        for tweet in tweets:
            tweet_id = (tweet.tweet_id or "").strip()
            if not tweet_id:
                # A record without a primary identity can never be deduplicated.
                report.invalid += 1
                continue
            if tweet_id in seen:
                report.duplicates += 1
                continue
            seen.add(tweet_id)
            if not tweet.account and isinstance(unit, Account):
                tweet.account = unit.username
            fresh.append(tweet)

        if fresh:
            _append_jsonl(path, [tweet.to_dict() for tweet in fresh])
            report.inserted = len(fresh)
        return report

    # -- audit trail --------------------------------------------------------
    def record_run(self, record: RunRecord) -> None:
        path = self.runs_dir / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
        _append_jsonl(path, [record.to_dict()])

    # -- delivery state -----------------------------------------------------
    def delivery_state(self, output: str) -> dict[str, str]:
        records, _ = _read_jsonl(self.delivery_path(output))
        state: dict[str, str] = {}
        for record in records:
            key = record.get("identity_key")
            if key:
                # Append-only, so the latest line for a key wins.
                state[str(key)] = str(record.get("status", ""))
        return state

    def pending_for(self, output: str, keys: list[str]) -> list[str]:
        state = self.delivery_state(output)
        return [key for key in keys if state.get(key) != DELIVERY_SENT]

    def mark_delivered(
        self,
        output: str,
        keys: list[str],
        status: str = DELIVERY_SENT,
        detail: str = "",
    ) -> int:
        if not keys:
            return 0
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payloads = [
            DeliveryRecord(
                output=output,
                identity_key=key,
                status=status,
                detail=detail,
                attempted_at=stamp,
            ).to_dict()
            for key in keys
        ]
        _append_jsonl(self.delivery_path(output), payloads)
        return len(payloads)

    # -- raw capture --------------------------------------------------------
    def should_capture(self, *, failed: bool, parsed: int) -> bool:
        if self.raw_policy == "always":
            return True
        if self.raw_policy == "on_error":
            return failed or parsed == 0
        return False

    def capture_raw(
        self,
        scope: Account | str,
        provider: str,
        route: str,
        response: RawResponse,
    ) -> str | None:
        if not response.text:
            return None

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        extension = _EXTENSIONS.get(response.content_type.split(";")[0].strip().lower(), ".txt")
        directory = self.raw_dir / _safe_name(_unit_name(scope))
        path = directory / f"{stamp}__{_safe_name(provider)}__{_safe_name(route)}{extension}"

        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(response.text, encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"cannot write raw capture {path}: {exc}") from exc
        return str(path)

    # -- inspection ---------------------------------------------------------
    def summary(self) -> dict:
        """Per-unit record counts, for ``scripts/verify_storage.py``."""
        accounts: dict[str, int] = {}
        if self.accounts_dir.exists():
            for path in sorted(self.accounts_dir.glob("*.jsonl")):
                records, _ = _read_jsonl(path)
                accounts[path.stem] = len(records)

        items: dict[str, int] = {}
        if self.items_dir.exists():
            for path in sorted(self.items_dir.glob("*.jsonl")):
                records, _ = _read_jsonl(path)
                items[path.stem] = len(records)

        runs = 0
        if self.runs_dir.exists():
            for path in sorted(self.runs_dir.glob("*.jsonl")):
                records, _ = _read_jsonl(path)
                runs += len(records)

        deliveries: dict[str, int] = {}
        if self.delivery_dir.exists():
            for path in sorted(self.delivery_dir.glob("*.jsonl")):
                records, _ = _read_jsonl(path)
                deliveries[path.stem] = len(records)

        return {
            "data_dir": str(self.data_dir),
            "raw_policy": self.raw_policy,
            "accounts": accounts,
            "total_tweets": sum(accounts.values()),
            "items": items,
            "total_items": sum(items.values()),
            "run_records": runs,
            "deliveries": deliveries,
        }
