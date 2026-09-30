"""Inspect and validate what Storage has actually written.

This is an operator tool, not part of the pipeline. It exists because the
deliverable is *the data*, and "the data is fine" needs to be checkable without
reading JSONL by eye. It is also what the GitHub Actions workflow calls after a
run, so a structurally broken store is visible in the job log rather than three
weeks later when something downstream tries to read it.

There are two stores, and they are checked separately because they are
different things (freeze v3, section 11):

* ``data/items/*.jsonl`` — the **canonical** store of ``NormalizedItem``
  records, one file per source namespace. This is the data flow's output.
* ``data/accounts/*.jsonl`` — the Phase 1 **raw record archive**. Evidence kept
  for re-processing, not part of the acquisition result.

Checks performed:

1. every line of every file parses as JSON;
2. every canonical item carries the fields the contract guarantees
   (``identity_key``, ``source_id``, ``item_id``, ``fetched_at``) and carries
   content (a ``title`` or a ``content``);
3. ``identity_key`` agrees with ``source_id``/``item_id`` — a stored copy that
   contradicts its own components means a corrupt or hand-edited line;
4. no ``identity_key`` appears twice inside one source file, and no ``tweet_id``
   appears twice inside one archive file;
5. run records parse and have a known ``status``.

Exit code: ``0`` when clean, ``1`` when any check fails.

Usage::

    python scripts/verify_storage.py
    python scripts/verify_storage.py --data-dir /tmp/x-reader
    python scripts/verify_storage.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_ITEM_FIELDS = ("identity_key", "source_id", "item_id", "fetched_at")
REQUIRED_TWEET_FIELDS = ("tweet_id", "account", "url", "provider", "route", "fetched_at")
KNOWN_RUN_STATUSES = {"ok", "no_tweets", "error", "skipped"}


def _iter_lines(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if line:
                yield number, line


def check_items(items_dir: Path) -> dict:
    """Validate the canonical ``NormalizedItem`` store."""
    report: dict = {"files": [], "problems": []}

    if not items_dir.exists():
        return report

    for path in sorted(items_dir.glob("*.jsonl")):
        seen: set[str] = set()
        total = 0
        duplicates: list[str] = []
        malformed: list[int] = []
        missing: list[tuple[int, str]] = []

        for number, line in _iter_lines(path):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                malformed.append(number)
                report["problems"].append(f"items/{path.name}:{number}: invalid JSON ({exc.msg})")
                continue
            if not isinstance(record, dict):
                malformed.append(number)
                report["problems"].append(f"items/{path.name}:{number}: not a JSON object")
                continue

            total += 1
            for field in REQUIRED_ITEM_FIELDS:
                if not record.get(field):
                    missing.append((number, field))

            source_id = str(record.get("source_id") or "")
            item_id = str(record.get("item_id") or "")
            key = str(record.get("identity_key") or "")
            if source_id and item_id and key and key != f"{source_id}:{item_id}":
                report["problems"].append(
                    f"items/{path.name}:{number}: identity_key {key!r} contradicts "
                    f"{source_id}:{item_id}"
                )
            if not (record.get("title") or record.get("content")):
                report["problems"].append(
                    f"items/{path.name}:{number}: item carries no title and no content"
                )

            if key:
                if key in seen:
                    duplicates.append(key)
                seen.add(key)

        for number, field in missing:
            report["problems"].append(f"items/{path.name}:{number}: missing '{field}'")
        for key in sorted(set(duplicates)):
            report["problems"].append(f"items/{path.name}: duplicate identity_key {key}")

        report["files"].append(
            {
                "source": path.stem,
                "records": total,
                "unique_keys": len(seen),
                "duplicates": len(set(duplicates)),
                "malformed_lines": len(malformed),
            }
        )

    return report


def check_accounts(accounts_dir: Path) -> dict:
    """Validate the raw record archive (Phase 1 ``accounts/``)."""
    report: dict = {"files": [], "problems": []}

    if not accounts_dir.exists():
        return report

    for path in sorted(accounts_dir.glob("*.jsonl")):
        seen: set[str] = set()
        total = 0
        duplicates: list[str] = []
        malformed: list[int] = []
        missing: list[tuple[int, str]] = []

        for number, line in _iter_lines(path):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                malformed.append(number)
                report["problems"].append(f"{path.name}:{number}: invalid JSON ({exc.msg})")
                continue
            if not isinstance(record, dict):
                malformed.append(number)
                report["problems"].append(f"{path.name}:{number}: not a JSON object")
                continue

            total += 1
            for field in REQUIRED_TWEET_FIELDS:
                if not record.get(field):
                    missing.append((number, field))

            tweet_id = str(record.get("tweet_id") or "")
            if tweet_id:
                if tweet_id in seen:
                    duplicates.append(tweet_id)
                seen.add(tweet_id)

        for number, field in missing:
            report["problems"].append(f"{path.name}:{number}: missing '{field}'")
        for tweet_id in sorted(set(duplicates)):
            report["problems"].append(f"{path.name}: duplicate tweet_id {tweet_id}")

        report["files"].append(
            {
                "account": path.stem,
                "records": total,
                "unique_ids": len(seen),
                "duplicates": len(set(duplicates)),
                "malformed_lines": len(malformed),
            }
        )

    return report


def check_runs(runs_dir: Path) -> dict:
    report: dict = {"files": [], "problems": []}
    if not runs_dir.exists():
        return report

    for path in sorted(runs_dir.glob("*.jsonl")):
        total = 0
        by_status: dict[str, int] = {}
        for number, line in _iter_lines(path):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                report["problems"].append(f"{path.name}:{number}: invalid JSON ({exc.msg})")
                continue
            total += 1
            status = record.get("status")
            if status not in KNOWN_RUN_STATUSES:
                report["problems"].append(
                    f"{path.name}:{number}: unknown status {status!r}"
                )
            by_status[status] = by_status.get(status, 0) + 1

        report["files"].append({"file": path.name, "records": total, "by_status": by_status})

    return report


def render(report: dict) -> str:
    lines = ["", f"data dir: {report['data_dir']}", ""]

    lines.append("  items (canonical NormalizedItem store)")
    if not report["items"]["files"]:
        lines.append("    (none)")
    for entry in report["items"]["files"]:
        lines.append(
            f"    {entry['source']:<20} {entry['records']:>6} records  "
            f"{entry['unique_keys']:>6} unique keys  "
            f"{entry['duplicates']} dup  {entry['malformed_lines']} malformed"
        )

    lines.append("")
    lines.append("  accounts (raw record archive)")
    if not report["accounts"]["files"]:
        lines.append("    (none)")
    for entry in report["accounts"]["files"]:
        lines.append(
            f"    {entry['account']:<20} {entry['records']:>6} records  "
            f"{entry['unique_ids']:>6} unique ids  "
            f"{entry['duplicates']} dup  {entry['malformed_lines']} malformed"
        )

    lines.append("")
    lines.append("  runs")
    if not report["runs"]["files"]:
        lines.append("    (none)")
    for entry in report["runs"]["files"]:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(entry["by_status"].items()))
        lines.append(f"    {entry['file']:<20} {entry['records']:>6} records  {detail}")

    problems = report["problems"]
    lines.append("")
    if problems:
        lines.append(f"  PROBLEMS ({len(problems)})")
        for problem in problems[:50]:
            lines.append(f"    - {problem}")
        if len(problems) > 50:
            lines.append(f"    ... and {len(problems) - 50} more")
    else:
        lines.append("  OK — no structural problems found")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", default="data", help="storage directory (default: data)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    report = {
        "data_dir": str(data_dir),
        "items": check_items(data_dir / "items"),
        "accounts": check_accounts(data_dir / "accounts"),
        "runs": check_runs(data_dir / "runs"),
    }
    report["problems"] = (
        report["items"]["problems"]
        + report["accounts"]["problems"]
        + report["runs"]["problems"]
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render(report))

    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
