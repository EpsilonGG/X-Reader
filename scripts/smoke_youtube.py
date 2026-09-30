"""YouTube smoke test: real HTTP -> Parser -> Normalizer -> Storage.

Two legs, and they answer different questions.

**Default (offline) leg — always runnable.**
Starts a real HTTP server on a real socket, points the *real*
:class:`~infrastructure.http.client.HTTPClient` at it, and drives the whole chain
through the real factory and the real Runner:

    build_youtube_provider() -> HTTPClient.get() -> YoutubeParser
        -> YoutubeNormalizer -> JsonlStorage

Nothing is stubbed: a real socket is opened, a real HTTP response is parsed, real
status-code mapping runs, real JSONL is written and read back. This is the leg
that proves the transport path, and it is the one that runs in CI.

It also exercises the ``url`` override on a channel — the escape hatch for a
mirror — because that is how the local server is addressed.

**Live leg (``--live``) — optional, needs egress to youtube.com.**
Hits the real public channel feed, with no API key, and reports honestly. If
youtube.com is unreachable it says so and exits non-zero; it never pretends to
have succeeded.

    python scripts/smoke_youtube.py
    python scripts/smoke_youtube.py --live --channel-id UCBR8-60-B28hp2BmDPdntcQ
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runner import Runner  # noqa: E402
from config.schema import Config  # noqa: E402
from domain.errors import XReaderError  # noqa: E402
from domain.models.account import Account  # noqa: E402
from infrastructure.http.client import HTTPClient  # noqa: E402
from normalizers.youtube import YoutubeNormalizer  # noqa: E402
from parsers.youtube import YoutubeParser  # noqa: E402
from providers.factory import build_youtube_provider  # noqa: E402
from providers.youtube import feed_url_for  # noqa: E402
from storage.jsonl import JsonlStorage  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "youtube_channel_feed.xml"
CHANNEL_ID = "UCabcdefghijklmnopqrstuv"


def _say(message: str) -> None:
    print(message, flush=True)


def _serve(body: bytes) -> tuple[ThreadingHTTPServer, str]:
    """A real HTTP server on an ephemeral port. Returns ``(server, base_url)``."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if urlparse(self.path).path != "/feed":
                self.send_error(404, "not the feed")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/atom+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # keep the output readable
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def offline_leg() -> int:
    _say("=" * 72)
    _say("LEG 1 — real socket, real HTTPClient, real chain")
    _say("=" * 72)

    body = FIXTURE.read_bytes()
    server, base = _serve(body)
    data_dir = Path(tempfile.mkdtemp(prefix="xreader-youtube-"))
    feed_url = f"{base}/feed"

    try:
        config = Config(
            provider={"order": []},
            storage={"data_dir": str(data_dir), "store_raw_response": "never"},
            youtube_channels=[{"channel_id": CHANNEL_ID, "url": feed_url}],
        )
        client = HTTPClient(timeout=10, retries=0)
        provider = build_youtube_provider(config, client)
        if provider is None:
            _say("FAIL: no YouTube provider was built")
            return 1

        # --- 1. the provider really did resolve the override --------------
        resolved = provider.url_for(CHANNEL_ID)
        _say(f"channel    : {CHANNEL_ID}")
        _say(f"feed url   : {resolved}")
        if resolved != feed_url:
            _say("FAIL: the channel url override was not honoured")
            return 1

        # --- 2. real HTTP over a real socket ------------------------------
        response = provider.fetch(Account(username=CHANNEL_ID), "feed")
        _say(
            f"http       : {response.status_code} {response.content_type} "
            f"({len(response.text)} bytes) in {response.url}"
        )
        if response.status_code != 200:
            _say("FAIL: unexpected status")
            return 1

        # --- 3. parse + normalize ----------------------------------------
        raw = YoutubeParser().parse(response, Account(username=CHANNEL_ID))
        items = YoutubeNormalizer().normalize(raw)
        _say(f"parsed     : {len(raw)} videos")
        _say(f"normalized : {len(items)} items")
        if not items:
            _say("FAIL: nothing normalized")
            return 1
        for item in items:
            _say(f"  - {item.identity_key}")
            _say(f"      publisher={item.publisher!r} published={item.published_at}")
            _say(f"      url={item.canonical_url} media={len(item.media)}")

        # --- 4. store, then re-run to prove incrementality ---------------
        storage = JsonlStorage(data_dir=data_dir)
        first = storage.save(items)
        second = storage.save(items)
        _say(f"stored     : inserted={first.inserted} duplicates={first.duplicates}")
        _say(f"re-run     : inserted={second.inserted} duplicates={second.duplicates}")
        if first.inserted != len(items) or second.inserted != 0:
            _say("FAIL: storage is not incremental")
            return 1

        # --- 5. read back from disk --------------------------------------
        reloaded = storage.load_items()
        _say(f"read back  : {len(reloaded)} items from {storage.item_path(items[0].source_id).name}")
        if len(reloaded) != len(items):
            _say("FAIL: read-back lost items")
            return 1

        # --- 6. the real Runner, end to end ------------------------------
        runner = Runner(
            config=config,
            accounts=[],
            providers=[],
            storage=storage,
            youtube_channels=config.enabled_youtube_channels,
            youtube_provider=provider,
            reader=storage,
        )
        summary = runner.run()
        outcome = summary.outcomes[0]
        _say(
            f"runner     : kind={outcome.kind} status={outcome.status} "
            f"source_id={outcome.source_id} inserted={outcome.inserted}"
        )
        if outcome.status != "ok" or outcome.kind != "youtube":
            _say("FAIL: the runner did not report a healthy YouTube unit")
            return 1

        client.close()
        _say("")
        _say("LEG 1 PASSED — real socket, real HTTP, real storage, real runner")
        _say(f"(data written under {data_dir})")
        return 0
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(data_dir, ignore_errors=True)


def live_leg(channel_id: str) -> int:
    _say("")
    _say("=" * 72)
    _say(f"LEG 2 — live youtube.com feed for {channel_id}")
    _say("=" * 72)

    url = feed_url_for(channel_id)
    _say(f"GET {url}")
    client = HTTPClient(timeout=20, retries=1)
    try:
        response = client.get(url)
    except XReaderError as exc:
        _say(f"UNREACHABLE: {type(exc).__name__}: {exc}")
        _say("")
        _say("The live leg needs egress to youtube.com. A sandbox or CI runner that")
        _say("blocks it will report exactly this; the offline leg above still proves")
        _say("the transport path. Nothing is being claimed about the live feed.")
        return 1

    _say(f"http       : {response.status_code} {response.content_type} ({len(response.text)} bytes)")
    raw = YoutubeParser().parse(response, Account(username=channel_id))
    items = YoutubeNormalizer().normalize(raw)
    _say(f"parsed     : {len(raw)} videos, normalized {len(items)}")
    if not items:
        _say("FAIL: the live feed produced no items")
        return 1
    for item in items[:3]:
        _say(f"  - {item.identity_key} | {item.publisher} | {item.published_at}")
        _say(f"      {item.title!r}")
    _say("")
    _say("LEG 2 PASSED — a real YouTube channel feed parsed with no API key")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--live",
        action="store_true",
        help="also fetch the real youtube.com feed (needs egress)",
    )
    parser.add_argument(
        "--channel-id",
        default="UCBR8-60-B28hp2BmDPdntcQ",
        help="channel to use for the live leg",
    )
    args = parser.parse_args(argv)

    code = offline_leg()
    if args.live:
        code = live_leg(args.channel_id) or code
    return code


if __name__ == "__main__":
    sys.exit(main())
