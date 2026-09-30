"""X-Reader — entrypoint.

Runs one pass of::

    Unit -> Provider/Fetcher -> Raw Response -> Parser -> Raw record
                                                          -> Normalizer
                                                          -> NormalizedItem
                                                          -> Storage
                                                          -> Output (Telegram / QQ)

A **unit** is an X account (``accounts.yaml``), an RSS source
(``rss_sources``) or a YouTube channel (``youtube_channels``), all in
``config/config.yaml`` except the accounts. Every unit takes the same path; only
the parser and normalizer differ.

Delivery is the last step and is driven entirely by configuration: with no
output enabled, the run acquires and stops, which is a complete configuration
rather than a degraded one. Telegram and QQ are independent — either, both or
neither. No RSS rendering, no summary, no LLM.

Usage::

    python main.py                          # fetch every enabled unit
    python main.py --account jack           # one X account
    python main.py --no-rss                 # X and YouTube units only
    python main.py --no-youtube             # X and RSS units only
    python main.py --json                   # machine-readable report
    python main.py --strict                 # non-zero if any unit or delivery failed
    python main.py --data-dir /tmp/x-reader # write somewhere else

The exit code is the contract with GitHub Actions:

===== =========================================================
  0   the run completed and produced usable results
  1   the run completed but *every* attempted unit failed,
      or no provider was available at all
  2   X-Reader could not start (bad configuration)
===== =========================================================

Deliberately absent: any read of ``GITHUB_ACTIONS``, ``GITHUB_WORKSPACE`` or
any other CI environment variable. Business logic must run identically on a
laptop and in CI (freeze, section 15); the workflow file is responsible for
wiring the environment, not this program.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.registry import bindings
from app.runner import Runner
from app.runner import RunSummary
from config.loader import ACCOUNTS_PATH
from config.loader import CONFIG_PATH
from config.loader import load_accounts
from config.loader import load_config
from domain.errors import ConfigurationError
from infrastructure.http.client import HTTPClient
from outputs.factory import build_outputs
from providers.factory import build_providers
from providers.factory import build_rss_provider
from providers.factory import build_youtube_provider
from storage.jsonl import JsonlStorage

logger = logging.getLogger("x_reader")

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CANNOT_START = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="x-reader",
        description=(
            "Fetch X timelines and configured RSS feeds into the unified store. "
            "Acquisition + normalization + storage only."
        ),
    )
    parser.add_argument("--config", default=str(CONFIG_PATH), help="path to config.yaml")
    parser.add_argument("--accounts", default=str(ACCOUNTS_PATH), help="path to accounts.yaml")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="override storage.data_dir from the config",
    )
    parser.add_argument(
        "--account",
        action="append",
        default=None,
        metavar="USERNAME",
        help="only process this X account (repeatable)",
    )
    parser.add_argument(
        "--no-rss",
        action="store_true",
        help="skip the RSS sources declared in config.yaml",
    )
    parser.add_argument(
        "--no-youtube",
        action="store_true",
        help="skip the YouTube channels declared in config.yaml",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="override fetch.limit for this run",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the run report as JSON instead of text",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "exit non-zero when any unit fails or any delivery fails, "
            "not only when all units do"
        ),
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="only log warnings and errors")
    parser.add_argument("--verbose", "-v", action="store_true", help="log every attempt")
    return parser


def configure_logging(quiet: bool, verbose: bool) -> None:
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )


def _select_accounts(accounts, wanted: list[str] | None):
    """Filter accounts by ``--account``, keeping configuration order.

    An unknown handle is reported and ignored rather than silently producing an
    empty run — the failure mode that makes a mis-typed account name cost a
    whole CI cycle to notice.
    """
    if not wanted:
        return accounts
    by_key = {a.username.lower(): a for a in accounts}
    selected = []
    for name in wanted:
        account = by_key.get(name.strip().lstrip("@").lower())
        if account is None:
            logger.warning("account '%s' is not in accounts.yaml — ignored", name)
            continue
        selected.append(account)
    return selected


def render_report(summary: RunSummary) -> str:
    """Human-readable report. Aligned, greppable, and honest about failures."""
    lines: list[str] = []
    lines.append("")
    lines.append(f"run {summary.run_id}  ({summary.duration_ms} ms)")
    lines.append("")

    if summary.outcomes:
        width = max(len(o.account) for o in summary.outcomes)
        width = max(width, len("unit"))
        header = (
            f"  {'unit':<{width}}  {'kind':<6} {'status':<10} {'provider/route':<18} "
            f"{'new':>4} {'dup':>4} {'fetched':>7}"
        )
        lines.append(header)
        lines.append("  " + "-" * (len(header) - 2))
        for outcome in summary.outcomes:
            route = (
                f"{outcome.provider}/{outcome.route}"
                if outcome.provider
                else "-"
            )
            lines.append(
                f"  {outcome.account:<{width}}  {outcome.kind:<6} {outcome.status:<10} "
                f"{route:<18} {outcome.inserted:>4} {outcome.duplicates:>4} "
                f"{outcome.fetched:>7}"
            )
            if outcome.error:
                lines.append(f"  {'':<{width}}    -> {outcome.error_kind}: {outcome.error}")
        lines.append("")

    lines.append(
        f"  units:  {summary.accounts_ok} ok, {summary.accounts_empty} empty, "
        f"{summary.accounts_failed} failed, {summary.accounts_skipped} skipped"
    )
    lines.append(
        f"  items:  {summary.inserted} new, {summary.duplicates} duplicate "
        f"({summary.normalized} normalized from {summary.fetched} fetched)"
    )

    if summary.deliveries:
        for delivery in summary.deliveries:
            state = "ok" if delivery.ok else "FAILED"
            lines.append(
                f"  output: {delivery.output} — {state}: "
                f"{delivery.delivered} delivered, {delivery.failed} failed, "
                f"{delivery.skipped} skipped ({delivery.pending} pending)"
            )
            if delivery.detail:
                lines.append(f"          -> {delivery.detail}")

    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.quiet, args.verbose)

    # --- start-up: configuration -------------------------------------------
    try:
        config = load_config(args.config)
        accounts = load_accounts(args.accounts)
    except ConfigurationError as exc:
        logger.error("cannot start: %s", exc)
        return EXIT_CANNOT_START

    if args.limit is not None:
        if args.limit <= 0:
            logger.error("cannot start: --limit must be positive")
            return EXIT_CANNOT_START
        config.fetch.limit = args.limit

    storage = JsonlStorage.from_config(config.storage)
    if args.data_dir:
        storage = JsonlStorage(data_dir=args.data_dir, raw_policy=config.storage.store_raw_response)

    accounts = _select_accounts(accounts, args.account)
    rss_sources = [] if args.no_rss else config.enabled_rss_sources
    youtube_channels = [] if args.no_youtube else config.enabled_youtube_channels
    if not accounts and not rss_sources and not youtube_channels:
        logger.error(
            "cannot start: no units selected "
            "(no accounts, no RSS sources, no YouTube channels)"
        )
        return EXIT_CANNOT_START

    # --- start-up: providers ------------------------------------------------
    client = HTTPClient(timeout=config.http.timeout, retries=config.http.retries)
    try:
        providers = build_providers(config, client)
        rss_provider = build_rss_provider(config, client) if rss_sources else None
        youtube_provider = (
            build_youtube_provider(config, client) if youtube_channels else None
        )
    except ConfigurationError as exc:
        logger.error("cannot start: %s", exc)
        return EXIT_CANNOT_START

    if not providers and rss_provider is None and youtube_provider is None:
        # Every configured provider reported itself unavailable. That is not a
        # transient failure, so it is reported as a hard one.
        logger.error(
            "cannot start: no provider available (configured order: %s)",
            ", ".join(config.provider.order),
        )
        return EXIT_RUN_FAILED

    logger.debug("routes available: %s", ", ".join(bindings_short(providers)))

    # --- start-up: outputs --------------------------------------------------
    # Built from configuration only. An enabled-but-unconfigured Telegram still
    # yields an adapter, so the failure is reported at delivery time and the
    # items stay pending instead of the whole run refusing to start.
    outputs = build_outputs(config)
    if outputs:
        logger.debug("outputs enabled: %s", ", ".join(o.name for o in outputs))

    # --- run ----------------------------------------------------------------
    runner = Runner(
        config=config,
        accounts=accounts,
        providers=providers,
        storage=storage,
        # The raw X-tweet archive is evidence kept alongside the canonical
        # store; RSS re-parseability comes from captured response bodies.
        archive=storage,
        rss_sources=rss_sources,
        rss_provider=rss_provider,
        youtube_channels=youtube_channels,
        youtube_provider=youtube_provider,
        outputs=outputs,
        # Reading items back is a delivery need, not part of the acquisition
        # contract, so it is passed separately (storage.base.ItemReader).
        reader=storage,
    )
    try:
        summary = runner.run()
    except KeyboardInterrupt:
        logger.warning("interrupted")
        return EXIT_RUN_FAILED
    finally:
        for output in outputs:
            output.close()
        client.close()

    # --- report -------------------------------------------------------------
    if args.json:
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_report(summary))

    attempted = summary.attempted
    if attempted and summary.accounts_failed == attempted:
        logger.error("every attempted account failed")
        return EXIT_RUN_FAILED

    # Delivery failure is reported loudly but does not by itself make the run
    # non-zero: the exit code answers "did acquisition work?", and a Telegram
    # outage must not turn a successful fetch into a red build. `--strict` is
    # the opt-in for operators who want the stricter reading. Either way the
    # items stay pending, so the next run retries them without a refetch.
    if not summary.deliveries_ok:
        logger.error(
            "delivery failed for: %s",
            ", ".join(d.output for d in summary.deliveries if not d.ok),
        )
    if args.strict and (summary.accounts_failed or not summary.deliveries_ok):
        return EXIT_RUN_FAILED
    return EXIT_OK


def bindings_short(providers) -> list[str]:
    """``provider/route`` labels for the providers actually in play."""
    return [f"{p.name}/{r}" for p in providers for r in p.routes()]


if __name__ == "__main__":
    sys.exit(main())
