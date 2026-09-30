"""Delivery integration tests: the whole chain, with a real store on disk.

``tests/test_telegram_output.py`` proves the adapter works. This file proves the
*chain* works, which is a different claim::

    X/RSS Provider -> Parser -> Normalizer -> NormalizedItem -> Storage
        -> pending_for("telegram") -> TelegramOutput.emit() -> mark_delivered()

The store is a real ``JsonlStorage`` writing real files under ``tmp_path``, and
the pipeline runs through the real ``Runner``. Only the two edges are faked — the
provider (so no network) and the Bot API (so no credentials and no flakiness) —
because those are the two things that cannot be exercised honestly in a unit
suite.

What is actually being asserted here, beyond "it works":

* **Seen and Delivered stay separate.** A failed delivery must not un-see an
  item, and a delivered item must not be re-fetched. This is the exact failure
  the reference aggregator in this workspace has (it marks everything seen and
  publishes only a capped prefix), so it is worth a test of its own.
* **A failure is retried from storage, not by refetching.** The retry runs with
  no provider at all, which can only work if the item was read back from disk.
* **One output's state cannot affect another's.** Delivery state is namespaced,
  so a future QQ adapter inherits the isolation rather than having to add it.
"""
from __future__ import annotations

import http.server
import json
import threading
from datetime import datetime
from datetime import timezone
from pathlib import Path

import httpx
import pytest

import main as entrypoint
from app.runner import Runner
from config.schema import Config
from domain.models.account import Account
from domain.models.item import PRECISION_SECOND
from domain.models.item import SOURCE_X
from domain.models.item import NormalizedItem
from infrastructure.http.response import RawResponse
from outputs import factory as outputs_factory
from outputs.telegram import TelegramOutput
from providers.base import BaseProvider
from storage.base import DELIVERY_FAILED
from storage.base import DELIVERY_SENT
from storage.jsonl import JsonlStorage
from tests.conftest import fixture_text

TIMELINE = fixture_text("nitter_timeline.html")
VNOVEL_RSS = fixture_text("vnovel_rss_sample.xml")

TOKEN = "fake-token"
CHAT_ID = "-1001234567890"

FETCHED = datetime(2024, 9, 11, 19, 31, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# stubs and helpers
# --------------------------------------------------------------------------
class StubXProvider(BaseProvider):
    name = "nitter"
    routes_ = ("html",)

    def fetch(self, account: Account, route: str) -> RawResponse:
        return RawResponse(
            url="https://a.example/x",
            status_code=200,
            content_type="text/html",
            text=TIMELINE,
            fetched_at=FETCHED,
        )


class StubRssProvider(BaseProvider):
    name = "rss"
    routes_ = ("feed",)

    def fetch(self, account: Account, route: str) -> RawResponse:
        return RawResponse(
            url="https://v.example/rss.xml",
            status_code=200,
            content_type="application/rss+xml",
            text=VNOVEL_RSS,
            fetched_at=FETCHED,
        )


class Recorder:
    """A scripted Bot API, same seam as the adapter's unit tests."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append({"url": str(request.url), "body": body})
        reply = self.replies.pop(0) if self.replies else _ok()
        if isinstance(reply, Exception):
            raise reply
        return reply


def _ok() -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def _error(status: int, description: str) -> httpx.Response:
    return httpx.Response(
        status, json={"ok": False, "error_code": status, "description": description}
    )


def telegram_output(recorder: Recorder, **kwargs) -> TelegramOutput:
    return TelegramOutput(
        bot_token=kwargs.pop("bot_token", TOKEN),
        chat_id=kwargs.pop("chat_id", CHAT_ID),
        api_base="https://api.telegram.test",
        transport=httpx.MockTransport(recorder),
        sleep=lambda _seconds: None,
        **kwargs,
    )


def make_item(item_id: str, *, source_id: str = SOURCE_X, content: str = "hello") -> NormalizedItem:
    return NormalizedItem(
        source_id=source_id,
        item_id=item_id,
        title=f"Title {item_id}",
        content=content,
        publisher="jack",
        canonical_url=f"https://example.test/{source_id}/{item_id}",
        published_at="2024-09-11T19:31:00+00:00",
        published_at_raw="Sep 11, 2024 · 7:31 PM UTC",
        published_precision=PRECISION_SECOND,
        fetched_at="2024-09-11T19:31:05+00:00",
    )


def make_config(data_dir: Path) -> Config:
    """A minimal valid config, for tests that drive the Runner directly.

    Built in code rather than YAML because these tests never read it from disk —
    they hand it straight to ``Runner``.
    """
    return Config(
        provider={"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}},
        storage={"data_dir": str(data_dir), "store_raw_response": "never"},
    )


#: Full entrypoint configuration: one X account, one feed, Telegram on.
PROJECT_CONFIG = """
provider:
  order: [nitter]
  nitter:
    endpoints: [https://a.example]
    routes: [html]
http:
  timeout: 5
  retries: 0
fetch:
  limit: 20
storage:
  data_dir: {data_dir}
  store_raw_response: never
rss_sources:
  - id: vnovel
    url: https://v.example/rss.xml
telegram:
  enabled: true
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
"""

ACCOUNTS_YAML = "accounts:\n  - jack\n"


@pytest.fixture
def telegram_api(monkeypatch):
    """Point the *real* factory's adapter at a scripted transport.

    Monkeypatching at the factory boundary (rather than replacing
    ``build_outputs``) means the config → credentials → adapter path is still the
    production one; only the socket is substituted.
    """

    def _install(*replies) -> Recorder:
        recorder = Recorder(*replies)

        def build(**kwargs) -> TelegramOutput:
            kwargs.setdefault("transport", httpx.MockTransport(recorder))
            kwargs.setdefault("sleep", lambda _seconds: None)
            return TelegramOutput(**kwargs)

        monkeypatch.setattr(outputs_factory, "TelegramOutput", build)
        return recorder

    return _install


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", CHAT_ID)


@pytest.fixture
def project(tmp_path) -> tuple[Path, Path, Path]:
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(PROJECT_CONFIG.format(data_dir=data_dir), encoding="utf-8")
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text(ACCOUNTS_YAML, encoding="utf-8")
    return config_path, accounts_path, data_dir


def argv(project, *extra: str) -> list[str]:
    config_path, accounts_path, _ = project
    return ["--config", str(config_path), "--accounts", str(accounts_path), *extra]


@pytest.fixture
def stub_providers(monkeypatch):
    monkeypatch.setattr(
        entrypoint, "build_providers", lambda config, client: [StubXProvider()]
    )
    monkeypatch.setattr(
        entrypoint, "build_rss_provider", lambda config, client: StubRssProvider()
    )


def delivery_state(data_dir: Path, output: str = "telegram") -> dict[str, str]:
    """``identity_key -> latest status``, read from the delivery file."""
    path = data_dir / "delivery" / f"{output}.jsonl"
    if not path.exists():
        return {}
    state: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            state[record["identity_key"]] = record["status"]
    return state


# --------------------------------------------------------------------------
# the chain, end to end, through main()
# --------------------------------------------------------------------------
def test_x_and_rss_both_reach_telegram_in_one_run(
    project, stub_providers, telegram_api, credentials, capsys
):
    """The Phase 3 acceptance path: both input kinds, one delivery pass."""
    recorder = telegram_api(*[_ok()] * 40)

    code = entrypoint.main(argv(project))

    assert code == entrypoint.EXIT_OK
    _, _, data_dir = project

    # Both sources produced items...
    stored = sorted(p.stem for p in (data_dir / "items").glob("*.jsonl"))
    assert stored == ["vnovel", "x"]

    # ...and every one of them was delivered.
    state = delivery_state(data_dir)
    assert state, "nothing was recorded as delivered"
    assert set(state.values()) == {DELIVERY_SENT}
    assert len(state) == len(recorder.requests)

    report = capsys.readouterr().out
    assert "output: telegram" in report


def test_delivery_state_is_separate_from_the_item_store(project, stub_providers, telegram_api, credentials):
    """Delivered state lives in data/delivery/, never inside the items files."""
    telegram_api(*[_ok()] * 40)
    entrypoint.main(argv(project))
    _, _, data_dir = project

    assert (data_dir / "delivery" / "telegram.jsonl").exists()

    # The canonical store must not have grown a delivery column: it knows one
    # model, and that model has no notion of Telegram.
    for path in (data_dir / "items").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                assert "delivery" not in record
                assert "telegram" not in json.dumps(record).lower()


def test_a_second_run_delivers_nothing_new(project, stub_providers, telegram_api, credentials):
    """Duplicate protection: what was delivered is not sent again."""
    first = telegram_api(*[_ok()] * 40)
    entrypoint.main(argv(project))
    first_count = len(first.requests)
    assert first_count > 0

    second = telegram_api(*[_ok()] * 40)
    entrypoint.main(argv(project))

    assert second.requests == []
    assert first_count > 0


def test_a_failed_delivery_does_not_unsee_the_item(
    project, stub_providers, telegram_api, credentials
):
    """Seen and Delivered are different questions with different answers.

    A Telegram outage must leave the item *seen* (so it is not re-fetched and
    duplicated in the store) and *not delivered* (so it is retried). Conflating
    the two is how content is lost.
    """
    telegram_api(*[_error(500, "Internal Server Error")] * 40)

    code = entrypoint.main(argv(project))

    assert code == entrypoint.EXIT_OK  # acquisition succeeded
    _, _, data_dir = project

    state = delivery_state(data_dir)
    assert state
    assert set(state.values()) == {DELIVERY_FAILED}

    # Still stored, so it is still "seen" and will not be inserted again.
    stored_ids = set()
    for path in (data_dir / "items").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                stored_ids.add(f"{record['source_id']}:{record['item_id']}")
    assert stored_ids
    assert stored_ids == set(state)


def test_a_failed_delivery_is_retried_on_the_next_run(
    project, stub_providers, telegram_api, credentials
):
    telegram_api(*[_error(500, "Internal Server Error")] * 40)
    entrypoint.main(argv(project))
    _, _, data_dir = project
    assert set(delivery_state(data_dir).values()) == {DELIVERY_FAILED}

    recorder = telegram_api(*[_ok()] * 40)
    entrypoint.main(argv(project))

    assert len(recorder.requests) > 0
    assert set(delivery_state(data_dir).values()) == {DELIVERY_SENT}


def test_a_failed_delivery_is_retried_without_refetching(tmp_path):
    """The retry reads items back from disk — no provider is involved at all.

    The retry run has no accounts, no RSS sources and no providers. If it can
    still deliver, the item can only have come from storage, which is the
    property that makes "retry without refetch" real rather than aspirational.
    """
    data_dir = tmp_path / "data"
    storage = JsonlStorage(data_dir=str(data_dir), raw_policy="never")
    item = make_item("1")
    storage.save([item])
    config = make_config(data_dir)

    failing = telegram_output(Recorder(_error(500, "Internal Server Error")))
    first = Runner(
        config=config, accounts=[], providers=[], storage=storage,
        outputs=[failing], reader=storage,
    ).run()

    assert first.delivery_failed == 1
    assert storage.pending_for("telegram", [item.identity_key]) == [item.identity_key]

    recorder = Recorder(_ok())
    second = Runner(
        config=config, accounts=[], providers=[], storage=storage,
        outputs=[telegram_output(recorder)], reader=storage,
    ).run()

    assert second.delivered == 1
    assert len(recorder.requests) == 1
    assert storage.pending_for("telegram", [item.identity_key]) == []


# --------------------------------------------------------------------------
# missing credentials (task section 17)
# --------------------------------------------------------------------------
def test_enabled_without_credentials_fails_explicitly_and_keeps_items_pending(
    project, stub_providers, telegram_api, monkeypatch, capsys
):
    """No silent skip: the run says so, and nothing is marked delivered."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    recorder = telegram_api()

    code = entrypoint.main(argv(project))

    assert recorder.requests == []  # never even tried to call the API
    _, _, data_dir = project

    state = delivery_state(data_dir)
    assert state
    assert set(state.values()) == {DELIVERY_FAILED}

    # The failure is written down, and no credential value is anywhere in it.
    raw = (data_dir / "delivery" / "telegram.jsonl").read_text(encoding="utf-8")
    assert "bot token" in raw
    assert TOKEN not in raw
    assert CHAT_ID not in raw

    report = capsys.readouterr().out
    assert "FAILED" in report
    assert "bot token" in report


def test_missing_credentials_do_not_stop_acquisition(project, stub_providers, telegram_api, monkeypatch):
    """The item store is filled regardless: delivery must not gate fetching."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    telegram_api()

    assert entrypoint.main(argv(project)) == entrypoint.EXIT_OK

    _, _, data_dir = project
    stored = sum(
        1
        for path in (data_dir / "items").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert stored > 0


def test_delivery_failure_is_exit_one_only_under_strict(
    project, stub_providers, telegram_api, credentials
):
    telegram_api(*[_error(500, "Internal Server Error")] * 40)
    assert entrypoint.main(argv(project)) == entrypoint.EXIT_OK

    telegram_api(*[_error(500, "Internal Server Error")] * 40)
    assert entrypoint.main(argv(project, "--strict")) == entrypoint.EXIT_RUN_FAILED


# --------------------------------------------------------------------------
# output isolation (task section 12)
# --------------------------------------------------------------------------
def test_one_outputs_state_does_not_affect_another(tmp_path):
    """``telegram`` and a future ``qq`` are independent namespaces.

    The QQ adapter does not exist yet (and is explicitly out of scope), but the
    isolation is a storage property and is testable now — which is the point of
    putting the namespace in the interface rather than in each adapter.
    """
    data_dir = tmp_path / "data"
    storage = JsonlStorage(data_dir=str(data_dir), raw_policy="never")
    item = make_item("1")
    storage.save([item])

    storage.mark_delivered("telegram", [item.identity_key], DELIVERY_SENT)

    assert storage.pending_for("telegram", [item.identity_key]) == []
    # The same item is still entirely undelivered for another output.
    assert storage.pending_for("qq", [item.identity_key]) == [item.identity_key]
    assert delivery_state(data_dir, "telegram") == {item.identity_key: DELIVERY_SENT}
    assert delivery_state(data_dir, "qq") == {}


def test_two_adapters_in_one_run_are_recorded_separately(tmp_path):
    """A second adapter sees everything the first one delivered — on purpose."""
    data_dir = tmp_path / "data"
    storage = JsonlStorage(data_dir=str(data_dir), raw_policy="never")
    items = [make_item("1"), make_item("2")]
    storage.save(items)
    config = make_config(data_dir)

    class Mirror(TelegramOutput):
        """Stands in for a second output; same seam, different name."""

        name = "mirror"

    telegram_recorder = Recorder(_ok(), _ok())
    mirror_recorder = Recorder(_ok(), _ok())

    summary = Runner(
        config=config, accounts=[], providers=[], storage=storage,
        outputs=[
            telegram_output(telegram_recorder),
            Mirror(
                bot_token=TOKEN, chat_id=CHAT_ID, api_base="https://api.telegram.test",
                transport=httpx.MockTransport(mirror_recorder), sleep=lambda _s: None,
            ),
        ],
        reader=storage,
    ).run()

    assert summary.delivered == 4  # two items × two outputs
    assert len(telegram_recorder.requests) == 2
    assert len(mirror_recorder.requests) == 2
    assert set(delivery_state(data_dir, "telegram").values()) == {DELIVERY_SENT}
    assert set(delivery_state(data_dir, "mirror").values()) == {DELIVERY_SENT}


# --------------------------------------------------------------------------
# the disabled path must be exactly the Phase 2 behaviour
# --------------------------------------------------------------------------
def test_no_output_configured_leaves_the_run_unchanged(tmp_path, monkeypatch):
    """Telegram off ⇒ no adapter, no delivery directory, no behaviour change."""
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        PROJECT_CONFIG.format(data_dir=data_dir).split("telegram:")[0],
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text(ACCOUNTS_YAML, encoding="utf-8")

    monkeypatch.setattr(entrypoint, "build_providers", lambda config, client: [StubXProvider()])
    monkeypatch.setattr(entrypoint, "build_rss_provider", lambda config, client: StubRssProvider())

    assert entrypoint.main(argv((config_path, accounts_path, data_dir))) == entrypoint.EXIT_OK

    assert not (data_dir / "delivery").exists()
    assert (data_dir / "items").exists()


def test_the_report_gains_no_delivery_lines_when_nothing_is_configured(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        PROJECT_CONFIG.format(data_dir=data_dir).split("telegram:")[0], encoding="utf-8"
    )
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text(ACCOUNTS_YAML, encoding="utf-8")
    monkeypatch.setattr(entrypoint, "build_providers", lambda config, client: [StubXProvider()])

    entrypoint.main(argv((config_path, accounts_path, data_dir)))

    report = capsys.readouterr().out
    assert "output:" not in report


# --------------------------------------------------------------------------
# over a real socket
# --------------------------------------------------------------------------
class BotHandler(http.server.BaseHTTPRequestHandler):
    """A minimal stand-in for the Bot API, on a real port."""

    #: Every request body the server received, as parsed JSON.
    received: list[dict] = []
    #: The path of every request, so the bot URL can be checked.
    paths: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        BotHandler.received.append(json.loads(raw) if raw else {})
        BotHandler.paths.append(self.path)

        body = json.dumps({"ok": True, "result": {"message_id": len(BotHandler.received)}})
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:  # silence the test output
        pass


@pytest.fixture
def bot_server():
    BotHandler.received = []
    BotHandler.paths = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), BotHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address
        yield f"http://{host}:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_delivery_over_a_real_socket(bot_server):
    """The real ``httpx`` client, a real POST, a real JSON response.

    Everything else in this file substitutes the transport. This one does not:
    it proves the URL, the JSON encoding and the response parsing work through
    the actual HTTP stack, which a mock cannot vouch for.
    """
    output = TelegramOutput(
        bot_token=TOKEN,
        chat_id=CHAT_ID,
        api_base=bot_server,
        timeout=5,
    )
    try:
        items = [make_item("1"), make_item("2")]
        result = output.emit(items)
    finally:
        output.close()

    assert result.ok is True
    assert result.delivered == 2

    # The real request path: /bot<token>/sendMessage
    assert BotHandler.paths == [f"/bot{TOKEN}/sendMessage"] * 2
    assert BotHandler.received[0]["chat_id"] == CHAT_ID
    assert BotHandler.received[0]["parse_mode"] == "HTML"
    assert "Title 1" in BotHandler.received[0]["text"]


def test_a_real_socket_failure_is_reported(bot_server, monkeypatch):
    """A refused connection is reported, not raised — over the real stack."""
    output = TelegramOutput(
        bot_token=TOKEN,
        chat_id=CHAT_ID,
        api_base="http://127.0.0.1:1",  # nothing listens here
        timeout=2,
    )
    try:
        result = output.emit([make_item("1")])
    finally:
        output.close()

    assert result.failed == 1
    assert TOKEN not in result.detail


# --------------------------------------------------------------------------
# config surface
# --------------------------------------------------------------------------
def test_a_token_written_into_the_config_is_rejected(tmp_path):
    """Secrets belong in the environment; config says so loudly, not silently.

    Pydantic ignores unknown keys by default, so without ``extra="forbid"`` this
    config would start fine, ignore the token, and then report "bot token is
    missing" while the operator looks straight at it.
    """
    from config.loader import load_config
    from domain.errors import ConfigurationError

    path = tmp_path / "config.yaml"
    path.write_text(
        """
provider:
  order: [nitter]
  nitter:
    endpoints: [https://a.example]
telegram:
  enabled: true
  bot_token: "123456:AAExampleTokenThatShouldNotBeHere"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "bot_token" in str(excinfo.value)


def test_an_enabled_block_with_no_variable_names_is_rejected(tmp_path):
    from config.loader import load_config
    from domain.errors import ConfigurationError

    path = tmp_path / "config.yaml"
    path.write_text(
        """
provider:
  order: [nitter]
  nitter:
    endpoints: [https://a.example]
telegram:
  enabled: true
  bot_token_env: ""
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "bot_token_env" in str(excinfo.value)


def test_a_disabled_block_may_be_incomplete(tmp_path):
    """Turning it off must not require completing it."""
    from config.loader import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        """
provider:
  order: [nitter]
  nitter:
    endpoints: [https://a.example]
telegram:
  enabled: false
""",
        encoding="utf-8",
    )

    config = load_config(path)
    assert config.telegram.enabled is False
    assert config.telegram.bot_token_env == "TELEGRAM_BOT_TOKEN"


def test_the_default_is_telegram_off(tmp_path):
    """No telegram block at all ⇒ off. Existing deployments are unaffected."""
    config = make_config(tmp_path / "data")
    assert config.telegram.enabled is False
    assert outputs_factory.build_outputs(config) == []


def test_the_factory_builds_an_adapter_even_without_credentials(monkeypatch):
    """Deliberate: the failure must surface as a delivery failure, not a no-op."""
    from config.schema import TelegramConfig

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    config = Config(
        provider={"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}},
        telegram=TelegramConfig(enabled=True),
    )

    outputs = outputs_factory.build_outputs(config)

    assert len(outputs) == 1
    assert outputs[0].configured is False
    assert "missing" in outputs[0].unavailable_reason()


def test_the_factory_reads_credentials_from_the_environment(monkeypatch):
    from config.schema import TelegramConfig

    monkeypatch.setenv("MY_TOKEN", TOKEN)
    monkeypatch.setenv("MY_CHAT", CHAT_ID)
    config = Config(
        provider={"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}},
        telegram=TelegramConfig(
            enabled=True, bot_token_env="MY_TOKEN", chat_id_env="MY_CHAT"
        ),
    )

    (output,) = outputs_factory.build_outputs(config)

    assert output.configured is True
