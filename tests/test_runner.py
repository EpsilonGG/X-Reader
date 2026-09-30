"""Runner tests.

The freeze's failure requirements are all enforced here:

* a single unit's failure must not damage any other unit's data;
* failures must be distinguishable by kind;
* a route failure falls through to the next route, then the next provider;
* a page with no items is "nothing to do", not an error;
* a broken storage layer must not be retried against other URLs.

Providers and parsers are faked where necessary; storage is real, because
storage *is* the deliverable.

Since Phase 2 the runner normalizes before it stores, so what lands on disk is a
``NormalizedItem``. The consequence that shows up most in these tests is
**identity is content-level, not unit-level**: the same tweet served to two
different timelines is one item, not two. Several tests below make that explicit
rather than working around it.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from datetime import timezone

import pytest

from app.runner import KIND_RSS
from app.runner import KIND_X
from app.runner import Runner
from app.runner import STATUS_ERROR
from app.runner import STATUS_NO_TWEETS
from app.runner import STATUS_OK
from app.runner import STATUS_SKIPPED
from config.schema import Config
from config.schema import RssSourceConfig
from domain.errors import NetworkError
from domain.errors import ProviderError
from domain.errors import StorageError
from domain.models.account import Account
from domain.models.item import SOURCE_X
from infrastructure.http.response import RawResponse
from providers.base import BaseProvider
from storage.jsonl import JsonlStorage
from tests.conftest import fixture_text

TIMELINE = fixture_text("nitter_timeline.html")
EMPTY_TIMELINE = fixture_text("empty_timeline.html")
NOT_A_TIMELINE = fixture_text("not_a_timeline.html")
VNOVEL_RSS = fixture_text("vnovel_rss_sample.xml")


def response(text: str = TIMELINE, url: str = "https://a.example/jack") -> RawResponse:
    return RawResponse(
        url=url,
        status_code=200,
        content_type="text/html",
        text=text,
        fetched_at=datetime(2024, 9, 11, 19, 31, tzinfo=timezone.utc),
    )


def timeline_for(handle: str) -> str:
    """The fixture timeline with ids shifted per handle.

    Two real accounts never serve the same tweets. Since identity is
    ``source_id:item_id``, reusing one fixture for two accounts would make the
    second account's timeline pure duplicates — which is *correct* behaviour but
    makes for a useless multi-unit test. Shifting the ids keeps the fixtures
    distinct the way real data is.
    """
    offset = (sum(ord(char) for char in handle) % 500 + 1) * 1000
    return re.sub(r"/status/(\d+)", lambda m: f"/status/{int(m.group(1)) + offset}", TIMELINE)


def feed_response(text: str = VNOVEL_RSS, url: str = "https://v.example/rss.xml") -> RawResponse:
    return RawResponse(
        url=url,
        status_code=200,
        content_type="application/rss+xml",
        text=text,
        fetched_at=datetime(2024, 9, 11, 19, 31, tzinfo=timezone.utc),
    )


class ScriptedProvider(BaseProvider):
    """A provider that does exactly what a test tells it to."""

    def __init__(self, name: str, script: dict[str, object]) -> None:
        self.name = name
        self._script = script
        self.routes_ = tuple(script)
        self.calls: list[tuple[str, str]] = []

    def available(self) -> bool:
        return True

    def fetch(self, account: Account, route: str) -> RawResponse:
        self.calls.append((account.username, route))
        outcome = self._script[route]
        if callable(outcome):
            outcome = outcome(account, route)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class BrokenStorage(JsonlStorage):
    """Fails to write, to prove the runner contains the damage.

    Storage now receives ``NormalizedItem`` batches rather than a unit plus
    tweets, so the failure is keyed on the batch's content instead of on a
    handle argument.
    """

    def __init__(self, *args, fail_for: set[str], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fail_for = fail_for
        self.save_calls: list[str] = []

    def save(self, items):
        batch = [item.metadata.get("account", "") for item in items]
        # One entry per *call*, so a test can assert how many times storage was
        # asked to write — which is the property under test.
        self.save_calls.append(batch[0] if batch else "")
        if any(account in self.fail_for for account in batch):
            raise StorageError(f"disk on fire for @{batch[0]}")
        return super().save(items)


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        **{
            "provider": {
                "order": ["nitter"],
                "nitter": {"endpoints": ["https://a.example"], "routes": ["html"]},
            },
            "fetch": {"limit": 20},
            "storage": {"data_dir": str(tmp_path / "data"), "store_raw_response": "never"},
        }
    )


@pytest.fixture
def storage(config) -> JsonlStorage:
    return JsonlStorage.from_config(config.storage)


def build(config, accounts, providers, storage, **kwargs) -> Runner:
    return Runner(
        config=config, accounts=accounts, providers=providers, storage=storage, **kwargs
    )


# --- reading helpers ------------------------------------------------------
def read_items(storage: JsonlStorage, source_id: str = SOURCE_X) -> list[dict]:
    """Every stored ``NormalizedItem`` for one source namespace."""
    path = storage.item_path(source_id)
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def items_for(storage: JsonlStorage, account: str, source_id: str = SOURCE_X) -> list[dict]:
    """Stored items whose tracked timeline was ``account``."""
    return [
        record for record in read_items(storage, source_id)
        if (record.get("metadata") or {}).get("account") == account
    ]


def run_records(storage: JsonlStorage) -> list[dict]:
    records: list[dict] = []
    for path in sorted(storage.runs_dir.glob("*.jsonl")):
        records += [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return records


# --- happy path -----------------------------------------------------------
def test_ok_path_stores_every_normalized_item(config, storage):
    providers = [ScriptedProvider("nitter", {"html": response()})]
    summary = build(config, [Account(username="jack")], providers, storage).run()

    assert summary.accounts_ok == 1
    assert summary.fetched == 8
    assert summary.normalized == 8
    assert summary.inserted == 8
    assert summary.outcomes[0].status == STATUS_OK
    assert summary.outcomes[0].kind == KIND_X
    assert len(read_items(storage)) == 8


def test_stored_records_are_normalized_items(config, storage):
    """Storage must receive the shared contract, never a raw tweet."""
    providers = [ScriptedProvider("nitter", {"html": response()})]
    build(config, [Account(username="jack")], providers, storage).run()

    for record in read_items(storage):
        assert record["source_id"] == SOURCE_X
        assert record["identity_key"] == f"x:{record['item_id']}"
        assert record["content"]
        assert "tweet_id" not in record and "raw" not in record


def test_runner_stamps_provenance_into_metadata(config, storage):
    """Parsers cannot know which provider served the bytes; the runner does.

    Provenance stays in ``metadata`` rather than becoming a contract field: the
    same tweet may be served by a different provider next run, and nothing
    downstream should branch on how it was reached.
    """
    providers = [ScriptedProvider("nitter", {"html": response()})]
    build(config, [Account(username="jack")], providers, storage).run()

    for record in read_items(storage):
        assert record["metadata"]["provider"] == "nitter"
        assert record["metadata"]["route"] == "html"
        assert record["metadata"]["account"] == "jack"
        assert record["fetched_at"]


def test_repeat_run_is_incremental_and_adds_no_duplicates(config, storage):
    providers = [ScriptedProvider("nitter", {"html": response()})]
    accounts = [Account(username="jack")]

    first = build(config, accounts, providers, storage).run()
    second = build(config, accounts, providers, storage).run()

    assert first.inserted == 8
    assert second.inserted == 0
    assert second.duplicates == 8
    # The file did not grow.
    assert len(read_items(storage)) == 8


def test_second_run_picks_up_only_the_new_tweet(config, storage):
    first_html = TIMELINE
    extra_item = """
      <div class="timeline-item">
        <a class="tweet-link" href="/jack/status/9999#m"></a>
        <div class="tweet-content">brand new</div>
      </div>
    """
    second_html = TIMELINE.replace("</div>\n</div>\n</body>", extra_item + "</div>\n</div>\n</body>")

    build(config, [Account(username="jack")],
          [ScriptedProvider("nitter", {"html": response(first_html)})], storage).run()
    summary = build(config, [Account(username="jack")],
                    [ScriptedProvider("nitter", {"html": response(second_html)})], storage).run()

    assert summary.inserted == 1
    assert summary.duplicates == 8


def test_the_same_tweet_in_two_timelines_is_stored_once(config, storage):
    """Identity is content-level. A shared tweet is one item, not two."""
    providers = [ScriptedProvider("nitter", {"html": response()})]
    accounts = [Account(username="jack"), Account(username="OpenAI")]
    summary = build(config, accounts, providers, storage).run()

    assert summary.fetched == 16
    assert summary.inserted == 8
    assert summary.duplicates == 8
    assert len(read_items(storage)) == 8


# --- fallback -------------------------------------------------------------
def test_falls_through_from_rss_to_html(config, storage):
    providers = [
        ScriptedProvider(
            "nitter",
            {
                "rss": NetworkError("HTTP 503 — rss"),
                "html": response(),
            },
        )
    ]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]

    assert outcome.status == STATUS_OK
    assert outcome.route == "html"
    assert [a["route"] for a in outcome.attempts] == ["rss", "html"]
    assert outcome.attempts[0]["error_kind"] == "network_error"


def test_falls_through_from_one_provider_to_the_next(config, storage):
    providers = [
        ScriptedProvider("nitter", {"html": ProviderError("HTTP 403", status=403)}),
        ScriptedProvider("xtf", {"search": response(url="https://n.example/search")}),
    ]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]

    assert outcome.status == STATUS_OK
    assert outcome.provider == "xtf"
    assert len(read_items(storage)) == 8


def test_all_routes_empty_is_no_tweets_not_an_error(config, storage):
    providers = [ScriptedProvider("nitter", {"html": response(EMPTY_TIMELINE)})]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]

    assert outcome.status == STATUS_NO_TWEETS
    assert outcome.error is None
    assert outcome.healthy is True


def test_empty_route_then_productive_route_uses_the_productive_one(config, storage):
    providers = [
        ScriptedProvider(
            "nitter", {"rss": response(EMPTY_TIMELINE), "html": response()}
        )
    ]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]

    assert outcome.status == STATUS_OK
    assert outcome.route == "html"


def test_every_route_failing_is_an_error_with_causes(config, storage):
    providers = [
        ScriptedProvider(
            "nitter",
            {
                "rss": NetworkError("HTTP 503"),
                "html": ProviderError("HTTP 403", status=403),
            },
        )
    ]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]

    assert outcome.status == STATUS_ERROR
    assert outcome.error_kind == "all_providers_failed"
    assert "nitter/rss" in outcome.error and "nitter/html" in outcome.error


# --- failure isolation ----------------------------------------------------
def test_one_account_failing_does_not_touch_the_others(config, storage):
    """The single most important guarantee in the freeze."""
    def scripted(account, route):
        if account.username == "broken":
            raise NetworkError("HTTP 503")
        return response(timeline_for(account.username))

    providers = [ScriptedProvider("nitter", {"html": scripted})]
    accounts = [
        Account(username="good"),
        Account(username="broken"),
        Account(username="alsogood"),
    ]
    summary = build(config, accounts, providers, storage).run()

    assert summary.accounts_ok == 2
    assert summary.accounts_failed == 1
    assert len(items_for(storage, "good")) == 8
    assert len(items_for(storage, "alsogood")) == 8
    assert items_for(storage, "broken") == []


def test_parsing_failure_is_recorded_as_a_parsing_error(config, storage):
    providers = [ScriptedProvider("nitter", {"html": response(NOT_A_TIMELINE)})]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]

    assert outcome.status == STATUS_ERROR
    assert outcome.attempts[0]["error_kind"] == "parsing_error"


def test_storage_failure_stops_that_account_but_not_the_run(config):
    storage = BrokenStorage(config.storage.data_path, fail_for={"jack"})
    providers = [
        ScriptedProvider(
            "nitter",
            {
                "html": lambda account, route: response(timeline_for(account.username)),
                "rss": lambda account, route: response(timeline_for(account.username)),
            },
        )
    ]
    accounts = [Account(username="jack"), Account(username="fine")]
    summary = build(config, accounts, providers, storage).run()

    jack = next(o for o in summary.outcomes if o.account == "jack")
    fine = next(o for o in summary.outcomes if o.account == "fine")

    assert jack.status == STATUS_ERROR
    assert jack.attempts[-1]["error_kind"] == "storage_error"
    # Storage is broken for this unit, so the second route is not attempted.
    assert storage.save_calls.count("jack") == 1
    assert fine.status == STATUS_OK


def test_unexpected_provider_exception_is_contained(config, storage):
    def explode(account, route):
        raise RuntimeError("provider bug")

    providers = [ScriptedProvider("nitter", {"html": explode})]
    accounts = [Account(username="jack"), Account(username="fine")]
    summary = build(config, accounts, providers, storage).run()

    jack = next(o for o in summary.outcomes if o.account == "jack")
    assert jack.status == STATUS_ERROR
    assert jack.attempts[0]["error_kind"] == "unexpected_error"


def test_unexpected_parser_exception_is_contained(config, storage, monkeypatch):
    from app import registry

    class ExplodingParser:
        name = "exploding"

        def parse(self, response, account):
            raise RuntimeError("parser bug")

    monkeypatch.setitem(
        registry._BINDINGS, ("nitter", "html"), registry.Binding(ExplodingParser, "x")
    )

    providers = [ScriptedProvider("nitter", {"html": response()})]
    accounts = [Account(username="jack"), Account(username="fine")]
    summary = build(config, accounts, providers, storage).run()

    jack = next(o for o in summary.outcomes if o.account == "jack")
    assert jack.attempts[0]["error_kind"] == "unexpected_error"
    assert summary.accounts_failed == 2  # both accounts share the broken parser


def test_unexpected_normalizer_exception_is_contained(config, storage, monkeypatch):
    """A normalizer bug is ours, not upstream's — and must not abort the run."""
    from normalizers import registry as normalizer_registry

    class ExplodingNormalizer:
        name = "exploding"

        def normalize(self, records):
            raise RuntimeError("normalizer bug")

    monkeypatch.setitem(normalizer_registry._NORMALIZERS, "x", ExplodingNormalizer())

    providers = [ScriptedProvider("nitter", {"html": response()})]
    accounts = [Account(username="jack"), Account(username="fine")]
    summary = build(config, accounts, providers, storage).run()

    jack = next(o for o in summary.outcomes if o.account == "jack")
    assert jack.attempts[0]["error_kind"] == "normalizer_error"
    assert summary.accounts_failed == 2


def test_unbound_route_is_reported_as_a_configuration_error(config, storage, monkeypatch):
    from app import registry

    monkeypatch.delitem(registry._BINDINGS, ("nitter", "html"))

    providers = [ScriptedProvider("nitter", {"html": response()})]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]

    assert outcome.status == STATUS_ERROR
    assert outcome.attempts[0]["error_kind"] == "configuration_error"


def test_records_that_normalize_to_nothing_are_not_reported_as_ok(config, storage, monkeypatch):
    """Records arrived but none satisfied the contract: do not claim success."""
    from normalizers import registry as normalizer_registry

    class EmptyNormalizer:
        name = "empty"

        def normalize(self, records):
            return []

    monkeypatch.setitem(normalizer_registry._NORMALIZERS, "x", EmptyNormalizer())

    providers = [ScriptedProvider("nitter", {"html": response()})]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]

    assert outcome.status == STATUS_NO_TWEETS
    assert outcome.status != STATUS_OK
    assert read_items(storage) == []


# --- configuration handling ----------------------------------------------
def test_disabled_account_is_skipped_without_any_fetch(config, storage):
    providers = [ScriptedProvider("nitter", {"html": response()})]
    accounts = [Account(username="jack", enabled=False)]
    summary = build(config, accounts, providers, storage).run()

    assert summary.outcomes[0].status == STATUS_SKIPPED
    assert summary.accounts_skipped == 1
    assert providers[0].calls == []
    assert read_items(storage) == []


def test_no_providers_yields_an_error_per_account(config, storage):
    summary = build(config, [Account(username="jack")], [], storage).run()
    assert summary.accounts_failed == 1
    assert summary.outcomes[0].error_kind == "all_providers_failed"


def test_fetch_limit_caps_how_much_is_stored(config, storage):
    config.fetch.limit = 3
    providers = [ScriptedProvider("nitter", {"html": response()})]
    summary = build(config, [Account(username="jack")], providers, storage).run()

    assert summary.inserted == 3
    assert len(read_items(storage)) == 3


def test_fetch_limit_does_not_truncate_an_rss_feed(config, storage):
    """``fetch.limit`` protects against X pagination, not against a whole feed.

    A feed arrives as one document, so capping it can only discard real content —
    and the default ``on_error`` capture policy would keep no evidence either.
    The real reference feed has 24 items, more than the default limit of 20.
    """
    config.fetch.limit = 3
    summary = build(
        config, [], [], storage,
        rss_sources=[rss_source()],
        rss_provider=ScriptedProvider("rss", {"feed": feed_response()}),
    ).run()

    outcome = summary.outcomes[0]
    assert outcome.normalized == 24
    assert outcome.inserted == 23  # the feed lists one URL twice


# --- RSS units ------------------------------------------------------------
def rss_source(source_id: str = "vnovel", url: str = "https://v.example/rss.xml"):
    return RssSourceConfig(id=source_id, url=url)


def test_rss_source_is_fetched_parsed_normalized_and_stored(config, storage):
    """The whole Phase 2 RSS path, end to end."""
    provider = ScriptedProvider("rss", {"feed": feed_response()})
    summary = build(
        config, [], [], storage,
        rss_sources=[rss_source()],
        rss_provider=provider,
    ).run()

    assert len(summary.outcomes) == 1
    outcome = summary.outcomes[0]
    assert outcome.kind == KIND_RSS
    assert outcome.source_id == "vnovel"
    assert outcome.status == STATUS_OK
    assert outcome.normalized > 0

    stored = read_items(storage, "vnovel")
    assert len(stored) == outcome.inserted
    for record in stored:
        assert record["source_id"] == "vnovel"
        assert record["identity_key"].startswith("vnovel:")
        assert (record["title"] or record["content"])


def test_rss_units_do_not_collide_with_x_items(config, storage):
    """``x:84674`` and ``vnovel:84674`` must be able to coexist."""
    build(config, [], [], storage,
          rss_sources=[rss_source()],
          rss_provider=ScriptedProvider("rss", {"feed": feed_response()})).run()
    build(config, [Account(username="jack")], [ScriptedProvider("nitter", {"html": response()})],
          storage).run()

    assert storage.item_path("vnovel") != storage.item_path(SOURCE_X)
    assert all(key.startswith("vnovel:") for key in storage.known_keys("vnovel"))
    assert all(key.startswith("x:") for key in storage.known_keys(SOURCE_X))


def test_disabled_rss_source_is_skipped(config, storage):
    provider = ScriptedProvider("rss", {"feed": feed_response()})
    summary = build(
        config, [], [], storage,
        rss_sources=[RssSourceConfig(id="vnovel", url="https://v.example/rss.xml", enabled=False)],
        rss_provider=provider,
    ).run()

    assert summary.outcomes[0].status == STATUS_SKIPPED
    assert summary.outcomes[0].kind == KIND_RSS
    assert provider.calls == []


def test_a_broken_rss_source_does_not_stop_an_x_account(config, storage):
    """One dead feed must not take down a healthy timeline."""
    rss = ScriptedProvider("rss", {"feed": NetworkError("HTTP 503")})
    x = ScriptedProvider("nitter", {"html": response()})
    summary = build(
        config, [Account(username="jack")], [x], storage,
        rss_sources=[rss_source()],
        rss_provider=rss,
    ).run()

    by_kind = {outcome.kind: outcome for outcome in summary.outcomes}
    assert by_kind[KIND_X].status == STATUS_OK
    assert by_kind[KIND_RSS].status == STATUS_ERROR
    assert summary.accounts_failed == 1
    assert len(read_items(storage, SOURCE_X)) == 8


def test_rss_run_record_names_the_kind_and_source(config, storage):
    build(config, [], [], storage,
          rss_sources=[rss_source()],
          rss_provider=ScriptedProvider("rss", {"feed": feed_response()})).run()

    record = run_records(storage)[0]
    assert record["kind"] == KIND_RSS
    assert record["source_id"] == "vnovel"
    assert record["normalized"] > 0


# --- audit trail ----------------------------------------------------------
def test_a_run_record_is_written_for_every_unit(config, storage):
    providers = [ScriptedProvider("nitter", {"html": response()})]
    accounts = [Account(username="jack"), Account(username="OpenAI")]
    summary = build(config, accounts, providers, storage).run()

    records = run_records(storage)
    assert len(records) == 2
    assert {r["account"] for r in records} == {"jack", "OpenAI"}
    assert all(r["run_id"] == summary.run_id for r in records)
    assert all(r["status"] == STATUS_OK for r in records)
    assert all(r["kind"] == KIND_X for r in records)


def test_a_failed_account_still_leaves_an_audit_record(config, storage):
    providers = [ScriptedProvider("nitter", {"html": NetworkError("HTTP 503")})]
    build(config, [Account(username="jack")], providers, storage).run()

    record = run_records(storage)[0]
    assert record["status"] == STATUS_ERROR
    assert record["error_kind"] == "all_providers_failed"
    assert record["attempts"][0]["error_kind"] == "network_error"


def test_run_record_survives_a_failing_audit_write(config, monkeypatch):
    """Losing a log line must never lose fetched items."""
    storage = JsonlStorage.from_config(config.storage)
    monkeypatch.setattr(
        storage, "record_run", lambda record: (_ for _ in ()).throw(StorageError("no disk"))
    )
    providers = [ScriptedProvider("nitter", {"html": response()})]
    summary = build(config, [Account(username="jack")], providers, storage).run()

    assert summary.inserted == 8
    assert len(read_items(storage)) == 8


# --- raw capture ----------------------------------------------------------
def test_raw_response_captured_when_nothing_was_parsed(config):
    """Diagnostics matter most exactly when parsing produced nothing."""
    config.storage.store_raw_response = "on_error"
    storage = JsonlStorage.from_config(config.storage)
    providers = [ScriptedProvider("nitter", {"html": response(EMPTY_TIMELINE)})]
    build(config, [Account(username="jack")], providers, storage).run()

    captured = list((storage.raw_dir / "jack").glob("*.html"))
    assert len(captured) == 1
    assert "timeline" in captured[0].read_text(encoding="utf-8")


def test_raw_response_not_captured_on_a_healthy_parse(config, storage):
    providers = [ScriptedProvider("nitter", {"html": response()})]
    build(config, [Account(username="jack")], providers, storage).run()
    assert not (storage.raw_dir / "jack").exists()


def test_raw_capture_failure_does_not_lose_the_data(config, monkeypatch):
    storage = JsonlStorage.from_config(config.storage)
    monkeypatch.setattr(
        storage, "capture_raw", lambda *a, **k: (_ for _ in ()).throw(StorageError("no disk"))
    )
    providers = [ScriptedProvider("nitter", {"html": response(EMPTY_TIMELINE)})]
    outcome = build(config, [Account(username="jack")], providers, storage).run().outcomes[0]
    assert outcome.status == STATUS_NO_TWEETS


# --- raw archive (evidence, separate from the canonical store) ------------
def test_raw_tweets_are_archived_alongside_normalized_items(config):
    storage = JsonlStorage.from_config(config.storage)
    providers = [ScriptedProvider("nitter", {"html": response()})]
    build(config, [Account(username="jack")], providers, storage, archive=storage).run()

    # Canonical store: the shared contract.
    assert len(read_items(storage)) == 8
    # Raw archive: the parser's original extraction, kept for re-processing.
    assert storage.known_ids(Account(username="jack")) == {
        "1000000000000000001", "1000000000000000002", "1000000000000000003",
        "1000000000000000004", "1000000000000000005", "1000000000000000006",
        "1000000000000000007", "1000000000000000008",
    }


def test_raw_archive_failure_does_not_lose_the_items(config, monkeypatch):
    storage = JsonlStorage.from_config(config.storage)
    monkeypatch.setattr(
        storage, "archive_raw_tweets",
        lambda unit, tweets: (_ for _ in ()).throw(StorageError("no disk")),
    )
    providers = [ScriptedProvider("nitter", {"html": response()})]
    summary = build(
        config, [Account(username="jack")], providers, storage, archive=storage
    ).run()

    assert summary.inserted == 8
    assert len(read_items(storage)) == 8


def test_rss_records_are_not_written_to_the_raw_tweet_archive(config):
    """RSS has no raw-record archive yet; inventing one would freeze a wrong shape."""
    storage = JsonlStorage.from_config(config.storage)
    build(
        config, [], [], storage,
        archive=storage,
        rss_sources=[rss_source()],
        rss_provider=ScriptedProvider("rss", {"feed": feed_response()}),
    ).run()

    assert len(read_items(storage, "vnovel")) > 0
    assert not (storage.accounts_dir / "vnovel.jsonl").exists()


# --- summary --------------------------------------------------------------
def test_summary_aggregates_across_units(config, storage):
    providers = [ScriptedProvider(
        "nitter", {"html": lambda account, route: response(timeline_for(account.username))}
    )]
    accounts = [Account(username="a"), Account(username="b")]
    summary = build(config, accounts, providers, storage).run()

    assert summary.attempted == 2
    assert summary.fetched == 16
    assert summary.normalized == 16
    assert summary.inserted == 16
    assert summary.to_dict()["tweets_inserted"] == 16
    assert summary.to_dict()["records_normalized"] == 16
    assert len(summary.to_dict()["outcomes"]) == 2


def test_outcome_attempts_are_serialisable(config, storage):
    providers = [ScriptedProvider("nitter", {"html": NetworkError("HTTP 503")})]
    summary = build(config, [Account(username="jack")], providers, storage).run()
    # Must not raise: the report is printed as JSON in CI.
    assert json.dumps(summary.to_dict())
