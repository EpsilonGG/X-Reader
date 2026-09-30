"""End-to-end integration tests over a real socket.

Every other test fakes either the network or the provider. These do neither: a
real HTTP server runs on an ephemeral port, serves the fixtures from it, and the
actual pipeline runs through ``main()``:

    Account -> NitterProvider -> HTTPClient -> NitterRssParser/NitterHtmlParser
            -> XNormalizer -> NormalizedItem -> JsonlStorage

    rss source -> RssProvider -> HTTPClient -> RssParser
               -> RssNormalizer -> NormalizedItem -> JsonlStorage

Both chains converge on the same storage, which is the Phase 2 acceptance
criterion (task section 24). If these pass, the data flow works as a whole, not
just as a collection of units.

The server deliberately misbehaves in two ways to prove the failover path is
wired up for real: the RSS route returns a rate-limit page for one account, and
one account does not exist at all.

Two stores are checked, and they are different things:

* ``data/items/<source>.jsonl`` — the canonical ``NormalizedItem`` store;
* ``data/accounts/<unit>.jsonl`` — the Phase 1 raw archive, kept as evidence.
"""
from __future__ import annotations

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

import main as entrypoint
from tests.conftest import fixture_text

TIMELINE = fixture_text("nitter_timeline.html")
FEED = fixture_text("nitter_feed.xml")
NOT_A_TIMELINE = fixture_text("not_a_timeline.html")
VNOVEL_RSS = fixture_text("vnovel_rss_sample.xml")

CONFIG_YAML = """
provider:
  order: [nitter]
  nitter:
    endpoints: ['{endpoint}']
    routes: [rss, html]
http:
  timeout: 5
  retries: 0
fetch:
  limit: 20
storage:
  data_dir: '{data_dir}'
  store_raw_response: on_error
{rss_block}"""

RSS_BLOCK = """rss_sources:
  - id: vnovel
    url: '{endpoint}/vnovel/rss.xml'
"""

ACCOUNTS_YAML = """
accounts:
  - jack
  - OpenAI
"""


class NitterishHandler(http.server.BaseHTTPRequestHandler):
    """Serves the fixture timeline/feed, with two deliberate faults."""

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0]
        body, content_type, status = self._resolve(path)
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _resolve(self, path: str) -> tuple[str, str, int]:
        segments = [s for s in path.split("/") if s]
        if not segments:
            return "not found", "text/plain", 404

        username = segments[0]
        route = segments[1] if len(segments) > 1 else "html"

        if username == "vnovel":
            # A real RSS source, served as a real feed.
            return VNOVEL_RSS, "application/rss+xml", 200

        if username == "ghost":
            return "user not found", "text/plain", 404

        if route == "rss":
            if username == "OpenAI":
                # Simulate the instance refusing the feed: the runner must fall
                # through to the HTML route rather than losing the account.
                return NOT_A_TIMELINE, "text/html", 200
            return FEED, "application/rss+xml", 200

        return TIMELINE, "text/html", 200

    def log_message(self, *args) -> None:  # silence the test output
        pass


@pytest.fixture
def server():
    handler = functools.partial(NitterishHandler)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def write_project(tmp_path: Path, endpoint: str, *, with_rss: bool = False):
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML.format(
            endpoint=endpoint,
            data_dir=Path(data_dir).as_posix(),
            rss_block=RSS_BLOCK.format(endpoint=endpoint) if with_rss else "",
        ),
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text(ACCOUNTS_YAML, encoding="utf-8")
    return config_path, accounts_path, data_dir


@pytest.fixture
def project(tmp_path, server):
    return write_project(tmp_path, server)


@pytest.fixture
def project_with_rss(tmp_path, server):
    return write_project(tmp_path, server, with_rss=True)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_records(data_dir: Path, username: str) -> list[dict]:
    """The raw record archive, which keeps its Phase 1 shape."""
    return read_jsonl(data_dir / "accounts" / f"{username}.jsonl")


def read_items(data_dir: Path, source_id: str) -> list[dict]:
    """The canonical NormalizedItem store."""
    return read_jsonl(data_dir / "items" / f"{source_id}.jsonl")


def test_full_pipeline_fetches_parses_and_stores(project, capsys):
    config_path, accounts_path, data_dir = project
    code = entrypoint.main(
        ["--config", str(config_path), "--accounts", str(accounts_path), "--json"]
    )
    assert code == entrypoint.EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["accounts_ok"] == 2

    # jack came in over RSS: no counters, no reply/quote data.
    jack = read_records(data_dir, "jack")
    assert len(jack) == 4
    assert all(r["route"] == "rss" and r["provider"] == "nitter" for r in jack)
    assert all(r["stats"]["likes"] is None for r in jack)

    # OpenAI's RSS route served a non-feed page, so the runner fell through to
    # the HTML route and still collected everything.
    openai = read_records(data_dir, "OpenAI")
    assert len(openai) == 8
    assert all(r["route"] == "html" for r in openai)
    assert any(r["stats"]["likes"] == 9012 for r in openai)


def test_the_canonical_store_receives_normalized_items(project, capsys):
    """Storage gets the shared contract, not the raw tweet shape."""
    config_path, accounts_path, data_dir = project
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    items = read_items(data_dir, "x")
    assert items, "the canonical store must not be empty after a healthy run"
    for record in items:
        assert record["source_id"] == "x"
        assert record["identity_key"] == f"x:{record['item_id']}"
        assert record["fetched_at"]
        assert record["title"] or record["content"]
        # Raw-tweet-only fields must not leak into the contract.
        assert "tweet_id" not in record
        assert "raw" not in record


def test_x_items_are_attributed_to_their_tracked_timeline(project, capsys):
    config_path, accounts_path, data_dir = project
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    accounts = {r["metadata"]["account"] for r in read_items(data_dir, "x")}
    assert accounts == {"jack", "OpenAI"}


def test_records_are_deduplicated_across_two_real_runs(project, capsys):
    config_path, accounts_path, data_dir = project
    argv = ["--config", str(config_path), "--accounts", str(accounts_path), "--json"]

    entrypoint.main(argv)
    capsys.readouterr()
    entrypoint.main(argv)
    payload = json.loads(capsys.readouterr().out)

    assert payload["tweets_inserted"] == 0
    assert payload["tweets_duplicate"] == 12  # 4 from jack plus 8 from OpenAI
    assert len(read_records(data_dir, "jack")) == 4
    assert len(read_records(data_dir, "OpenAI")) == 8


def test_a_missing_account_does_not_break_a_healthy_one(tmp_path, server, capsys):
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML.format(
            endpoint=server, data_dir=Path(data_dir).as_posix(), rss_block=""
        ),
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("accounts:\n  - ghost\n  - jack\n", encoding="utf-8")

    code = entrypoint.main(
        ["--config", str(config_path), "--accounts", str(accounts_path), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["accounts_failed"] == 1
    assert payload["accounts_ok"] == 1
    assert read_records(data_dir, "ghost") == []
    assert len(read_records(data_dir, "jack")) == 4
    # Not every account failed, so the default exit code stays clean.
    assert code == entrypoint.EXIT_OK


# --- RSS chain, over a real socket ----------------------------------------
def test_rss_source_runs_end_to_end_over_http(project_with_rss, capsys):
    """The Phase 2 acceptance path for a web/RSS source, with real HTTP."""
    config_path, accounts_path, data_dir = project_with_rss
    assert entrypoint.main(
        ["--config", str(config_path), "--accounts", str(accounts_path), "--json"]
    ) == entrypoint.EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    outcome = next(o for o in payload["outcomes"] if o["kind"] == "rss")
    assert outcome["source_id"] == "vnovel"
    assert outcome["status"] == "ok"
    assert outcome["normalized"] == 24

    items = read_items(data_dir, "vnovel")
    assert len(items) == outcome["inserted"] == 23  # the feed lists one URL twice
    assert all(record["source_id"] == "vnovel" for record in items)
    assert all(record["metadata"]["identity_basis"] == "url" for record in items)
    assert any(record["media"] for record in items)


def test_both_chains_converge_on_the_same_store(project_with_rss, capsys):
    """X and RSS are different inputs that meet at NormalizedItem -> Storage."""
    config_path, accounts_path, data_dir = project_with_rss
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    assert read_items(data_dir, "x")
    assert read_items(data_dir, "vnovel")

    # Two namespaces, two files, no shared identity space.
    keys = {
        "x": {r["identity_key"] for r in read_items(data_dir, "x")},
        "vnovel": {r["identity_key"] for r in read_items(data_dir, "vnovel")},
    }
    assert all(key.startswith("x:") for key in keys["x"])
    assert all(key.startswith("vnovel:") for key in keys["vnovel"])
    assert keys["x"] & keys["vnovel"] == set()

    # And the run record distinguishes them.
    runs = read_jsonl(next(iter(sorted((data_dir / "runs").glob("*.jsonl")))))
    kinds = {record["kind"] for record in runs}
    assert kinds == {"x", "rss"}


def test_rss_only_feed_is_not_written_to_the_raw_tweet_archive(project_with_rss, capsys):
    config_path, accounts_path, data_dir = project_with_rss
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    assert not (data_dir / "accounts" / "vnovel.jsonl").exists()


# --- the verification script ----------------------------------------------
def test_verify_storage_script_passes_on_real_output(project, capsys):
    config_path, accounts_path, data_dir = project
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    import scripts.verify_storage as verify

    assert verify.main(["--data-dir", str(data_dir), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["problems"] == []
    assert {f["account"] for f in report["accounts"]["files"]} == {"jack", "OpenAI"}
    assert {f["source"] for f in report["items"]["files"]} == {"x"}


def test_verify_storage_script_checks_the_canonical_store(project_with_rss, capsys):
    config_path, accounts_path, data_dir = project_with_rss
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    import scripts.verify_storage as verify

    assert verify.main(["--data-dir", str(data_dir), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert {f["source"] for f in report["items"]["files"]} == {"x", "vnovel"}


def test_verify_storage_script_detects_a_duplicate(project, capsys):
    config_path, accounts_path, data_dir = project
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    # Hand-craft a duplicate line, which storage itself would never write.
    path = data_dir / "accounts" / "jack.jsonl"
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(first_line + "\n")

    import scripts.verify_storage as verify

    assert verify.main(["--data-dir", str(data_dir)]) == 1
    out = capsys.readouterr().out
    assert "duplicate tweet_id" in out


def test_verify_storage_script_detects_a_duplicate_identity_key(project, capsys):
    config_path, accounts_path, data_dir = project
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    path = data_dir / "items" / "x.jsonl"
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(first_line + "\n")

    import scripts.verify_storage as verify

    assert verify.main(["--data-dir", str(data_dir)]) == 1
    out = capsys.readouterr().out
    assert "duplicate identity_key" in out


def test_verify_storage_script_detects_a_self_contradicting_line(project, capsys):
    config_path, accounts_path, data_dir = project
    entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    capsys.readouterr()

    path = data_dir / "items" / "x.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    record["item_id"] = "tampered"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    import scripts.verify_storage as verify

    assert verify.main(["--data-dir", str(data_dir)]) == 1
    out = capsys.readouterr().out
    assert "contradicts" in out
