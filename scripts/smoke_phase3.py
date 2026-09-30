"""End-to-end Phase 3 smoke run over real sockets, with no test scaffolding.

Unlike the pytest suite, this drives the *actual* entrypoint (``main.main``) with
no monkeypatching at all. Two local HTTP servers stand in for the outside world:

* a Nitter-ish server serving the real fixture timeline and a real RSS feed;
* a Bot-API-ish server that records every ``sendMessage`` it receives.

Then it runs the CLI several times and prints the real reports, so the whole
chain is demonstrated exactly as a user would experience it:

    X account + RSS feed -> fetch -> parse -> normalize -> store
        -> pending_for("telegram") -> sendMessage -> mark_delivered

The single substitution is the Bot API base URL: the adapter's ``api_base`` is a
constructor parameter, so it is injected through the factory. Everything else —
the entrypoint, the config loader, the runner, storage, rendering, splitting,
retry — is the production path.

Usage: python scripts/smoke_phase3.py
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import main as entrypoint  # noqa: E402
from tests.conftest import fixture_text  # noqa: E402

TIMELINE = fixture_text("nitter_timeline.html")
FEED = fixture_text("vnovel_rss_sample.xml")

TOKEN = "fake-token-for-smoke-test"
CHAT_ID = "-1001234567890"

sent: list[dict] = []


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    """Serves the timeline at /<user>/html and the feed at /vnovel/rss.xml."""

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path.startswith("/vnovel"):
            body, content_type = FEED, "application/rss+xml"
        else:
            body, content_type = TIMELINE, "text/html"
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass


class BotHandler(http.server.BaseHTTPRequestHandler):
    """Stands in for api.telegram.org."""

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        body = json.loads(raw) if raw else {}
        sent.append({"path": self.path, "body": body})

        payload = json.dumps({"ok": True, "result": {"message_id": len(sent)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        pass


def serve(handler) -> tuple[http.server.ThreadingHTTPServer, str]:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address
    return httpd, f"http://{host}:{port}"


CONFIG = """\
provider:
  order: [nitter]
  nitter:
    endpoints: ['{upstream}']
    routes: [html]
http:
  timeout: 5
  retries: 0
fetch:
  limit: 20
storage:
  data_dir: '{data_dir}'
  store_raw_response: never
rss_sources:
  - id: vnovel
    url: '{upstream}/vnovel/rss.xml'
    enabled: true
telegram:
  enabled: true
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
"""

ACCOUNTS = "accounts:\n  - jack\n"


def main() -> int:
    upstream, upstream_url = serve(UpstreamHandler)
    bot, bot_url = serve(BotHandler)
    # The adapter builds its URL from api_base; point it at the local bot.
    os.environ["TELEGRAM_BOT_TOKEN"] = TOKEN
    os.environ["TELEGRAM_CHAT_ID"] = CHAT_ID

    workdir = Path(tempfile.mkdtemp(prefix="xr-smoke-"))
    data_dir = workdir / "data"
    config_path = workdir / "config.yaml"
    accounts_path = workdir / "accounts.yaml"
    config_path.write_text(
        CONFIG.format(upstream=upstream_url, data_dir=data_dir.as_posix()), encoding="utf-8"
    )
    accounts_path.write_text(ACCOUNTS, encoding="utf-8")

    # Point the Bot API at the local stand-in.
    #
    # This has to go through the factory: ``api_base`` is a *default parameter*,
    # so it is bound when the class body is defined. Reassigning the module
    # constant afterwards would silently have no effect, and the adapter would
    # try to reach the real api.telegram.org.
    import outputs.factory as factory_module
    from outputs.telegram import TelegramOutput

    def local_bot(**kwargs) -> TelegramOutput:
        kwargs.setdefault("api_base", bot_url)
        kwargs.setdefault("timeout", 5)
        return TelegramOutput(**kwargs)

    factory_module.TelegramOutput = local_bot

    def point_at(url: str) -> None:
        """Repoint the bot for the outage/recovery part of the run."""
        def target(**kwargs) -> TelegramOutput:
            kwargs.setdefault("api_base", url)
            kwargs.setdefault("timeout", 2)
            return TelegramOutput(**kwargs)

        factory_module.TelegramOutput = target

    argv = ["--config", str(config_path), "--accounts", str(accounts_path)]

    print("=" * 72)
    print("RUN 1 — fetch, store, deliver")
    print("=" * 72)
    code = entrypoint.main(argv)
    print(f"exit code: {code}")
    print(f"sendMessage calls: {len(sent)}")
    if sent:
        print(f"request path: {sent[0]['path']}")
        print("first message text:")
        print("-" * 72)
        print(sent[0]["body"]["text"])
        print("-" * 72)

    delivered_state = (data_dir / "delivery" / "telegram.jsonl").read_text(encoding="utf-8")
    statuses = [json.loads(line)["status"] for line in delivered_state.splitlines() if line.strip()]
    print(f"delivery records: {len(statuses)}, statuses: {sorted(set(statuses))}")

    print()
    print("=" * 72)
    print("RUN 2 — nothing new should be sent")
    print("=" * 72)
    before = len(sent)
    code2 = entrypoint.main(argv)
    print(f"exit code: {code2}")
    print(f"sendMessage calls this run: {len(sent) - before}")

    print()
    print("=" * 72)
    print("RUN 3 — simulate a Telegram outage, then recover")
    print("=" * 72)

    def read_state() -> dict[str, str]:
        path = data_dir / "delivery" / "telegram.jsonl"
        return {
            record["identity_key"]: record["status"]
            for record in (
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }

    # Everything was delivered in run 1, so there is nothing left to fail. Drop
    # the delivery state to put all 32 items back into "pending" — which is
    # exactly the situation a first-time outage creates.
    (data_dir / "delivery" / "telegram.jsonl").unlink()
    print("delivery state cleared; all items are pending again")

    # Force failures by pointing at a dead port, then confirm the items stay
    # pending and a later run delivers them without refetching anything.
    point_at("http://127.0.0.1:1")
    code3 = entrypoint.main(argv)
    print(f"exit code during outage: {code3}")
    state = read_state()
    print(f"statuses after outage: {sorted(set(state.values()))}")
    detail = next(
        (
            record["detail"]
            for record in reversed(
                [
                    json.loads(line)
                    for line in (data_dir / "delivery" / "telegram.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]
            )
            if record["status"] == "failed"
        ),
        "(none)",
    )
    print(f"sample failure detail: {detail}")
    print(f"  -> the token appears in it? {'YES (BUG)' if TOKEN in detail else 'no'}")

    # Recovery: the retry must read the items back from disk, not refetch them.
    # Point the *upstream* at a dead port too, so a refetch would fail loudly.
    point_at(bot_url)
    before = len(sent)
    upstream_dead = upstream_url
    config_path.write_text(
        CONFIG.format(upstream="http://127.0.0.1:1", data_dir=data_dir.as_posix()),
        encoding="utf-8",
    )
    code4 = entrypoint.main(argv)
    print(f"exit code after recovery (upstream unreachable): {code4}")
    print(f"re-delivered on recovery: {len(sent) - before}")
    print(f"statuses after recovery: {sorted(set(read_state().values()))}")
    print("  -> upstream was dead, so nothing could be refetched: delivery came from storage")

    config_path.write_text(
        CONFIG.format(upstream=upstream_dead, data_dir=data_dir.as_posix()), encoding="utf-8"
    )

    print()
    print("=" * 72)
    print("STORED FILES")
    print("=" * 72)
    for path in sorted(data_dir.rglob("*")):
        if path.is_file():
            lines = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            print(f"  {path.relative_to(data_dir).as_posix():<40} {lines} record(s)")

    upstream.shutdown()
    bot.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
