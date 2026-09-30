"""Entrypoint tests.

``main()`` is the contract with GitHub Actions, so its exit codes are tested
explicitly: 0 for a usable run, 1 for a run that produced nothing, 2 for a
start-up failure. The workflow file relies on this distinction to tell
"the job worked" from "the job needs a human".
"""
from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

import main as entrypoint
from domain.errors import NetworkError
from domain.models.account import Account
from infrastructure.http.response import RawResponse
from providers.base import BaseProvider
from tests.conftest import fixture_text

TIMELINE = fixture_text("nitter_timeline.html")
EMPTY_TIMELINE = fixture_text("empty_timeline.html")

CONFIG_YAML = """
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
"""

#: The same, plus one RSS source. Kept separate so the X-only tests cannot
#: accidentally attempt a network fetch of a feed.
RSS_CONFIG_YAML = CONFIG_YAML + """
rss_sources:
  - id: vnovel
    url: https://v.example/rss.xml
"""

#: X input disabled entirely: an empty ``provider.order`` means "no X", and the
#: only input is one feed. This is the configuration the Phase 2 decoupling step
#: made legal — before it, an empty order was rejected outright.
RSS_ONLY_CONFIG_YAML = """
provider:
  order: []
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
"""

ACCOUNTS_YAML = """
accounts:
  - jack
  - OpenAI
"""

VNOVEL_RSS = fixture_text("vnovel_rss_sample.xml")


class StubProvider(BaseProvider):
    name = "nitter"
    routes_ = ("html",)

    def __init__(self, text: str = TIMELINE, error: Exception | None = None) -> None:
        self.text = text
        self.error = error

    def fetch(self, account: Account, route: str) -> RawResponse:
        if self.error is not None:
            raise self.error
        return RawResponse(
            url="https://a.example/x",
            status_code=200,
            content_type="text/html",
            text=self.text,
            fetched_at=datetime(2024, 9, 11, 19, 31, tzinfo=timezone.utc),
        )


class PickyProvider(BaseProvider):
    """Succeeds for every handle except one, to exercise partial failure."""

    name = "nitter"
    routes_ = ("html",)

    def __init__(self, failing: str = "OpenAI") -> None:
        self.failing = failing

    def fetch(self, account: Account, route: str) -> RawResponse:
        if account.username == self.failing:
            raise NetworkError("HTTP 503")
        return RawResponse(
            url="https://a.example/x",
            status_code=200,
            content_type="text/html",
            text=TIMELINE,
            fetched_at=datetime(2024, 9, 11, 19, 31, tzinfo=timezone.utc),
        )


class StubRssProvider(BaseProvider):
    """Serves the real VNovel feed body with no network access."""

    name = "rss"
    routes_ = ("feed",)

    def __init__(self, text: str = VNOVEL_RSS) -> None:
        self.text = text

    def fetch(self, account: Account, route: str) -> RawResponse:
        return RawResponse(
            url="https://v.example/rss.xml",
            status_code=200,
            content_type="application/rss+xml",
            text=self.text,
            fetched_at=datetime(2024, 9, 11, 19, 31, tzinfo=timezone.utc),
        )


@pytest.fixture
def project(tmp_path) -> tuple[Path, Path, Path]:
    """A throwaway config + accounts file and a data directory."""
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML.format(data_dir=data_dir), encoding="utf-8")
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text(ACCOUNTS_YAML, encoding="utf-8")
    return config_path, accounts_path, data_dir


@pytest.fixture
def mixed_project(tmp_path) -> tuple[Path, Path, Path]:
    """One X account *and* one RSS source — the Phase 2 goal state."""
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(RSS_CONFIG_YAML.format(data_dir=data_dir), encoding="utf-8")
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("accounts:\n  - jack\n", encoding="utf-8")
    return config_path, accounts_path, data_dir


@pytest.fixture
def rss_only_project(tmp_path) -> tuple[Path, Path, Path]:
    """No X at all: an empty provider chain, no accounts, one feed."""
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(RSS_ONLY_CONFIG_YAML.format(data_dir=data_dir), encoding="utf-8")
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("accounts: []\n", encoding="utf-8")
    return config_path, accounts_path, data_dir


def argv(project, *extra: str) -> list[str]:
    config_path, accounts_path, _ = project
    return ["--config", str(config_path), "--accounts", str(accounts_path), *extra]


@pytest.fixture
def stub_providers(monkeypatch):
    """Replace provider construction so no network call can happen."""

    def _install(provider: BaseProvider | None) -> None:
        monkeypatch.setattr(
            entrypoint, "build_providers", lambda config, client: ([provider] if provider else [])
        )

    return _install


@pytest.fixture
def stub_rss_provider(monkeypatch):
    """Replace the RSS provider construction so no network call can happen."""

    def _install(provider: BaseProvider | None) -> None:
        monkeypatch.setattr(
            entrypoint, "build_rss_provider",
            lambda config, client: provider,
        )

    return _install


# --- start-up failures (exit 2) -------------------------------------------
def test_missing_config_returns_cannot_start(tmp_path):
    code = entrypoint.main(["--config", str(tmp_path / "nope.yaml"),
                            "--accounts", str(tmp_path / "also-nope.yaml")])
    assert code == entrypoint.EXIT_CANNOT_START


def test_missing_accounts_returns_cannot_start(project):
    config_path, _, _ = project
    code = entrypoint.main(["--config", str(config_path),
                            "--accounts", str(config_path.parent / "nope.yaml")])
    assert code == entrypoint.EXIT_CANNOT_START


def test_zero_limit_returns_cannot_start(project):
    assert entrypoint.main(argv(project, "--limit", "0")) == entrypoint.EXIT_CANNOT_START


def test_account_filter_matching_nothing_returns_cannot_start(project, stub_providers):
    stub_providers(StubProvider())
    assert entrypoint.main(argv(project, "--account", "nobody")) == (
        entrypoint.EXIT_CANNOT_START
    )


def test_no_available_provider_returns_run_failed(project, stub_providers):
    stub_providers(None)
    assert entrypoint.main(argv(project)) == entrypoint.EXIT_RUN_FAILED


# --- successful runs (exit 0) ---------------------------------------------
def test_successful_run_returns_zero_and_persists(project, stub_providers, capsys):
    stub_providers(StubProvider())
    _, _, data_dir = project

    assert entrypoint.main(argv(project)) == entrypoint.EXIT_OK

    # Both accounts are served the same fixture, so identity dedup collapses
    # them into one set of items. That is the Phase 2 contract, not a bug.
    stored = (data_dir / "items" / "x.jsonl").read_text(encoding="utf-8")
    assert len([line for line in stored.splitlines() if line]) == 8


def test_raw_tweets_are_archived_in_the_phase_one_location(project, stub_providers):
    """The archive keeps its Phase 1 path; the canonical store is new."""
    stub_providers(StubProvider())
    _, _, data_dir = project
    entrypoint.main(argv(project))

    archived = (data_dir / "accounts" / "jack.jsonl").read_text(encoding="utf-8")
    assert len([line for line in archived.splitlines() if line]) == 8
    assert (data_dir / "accounts" / "OpenAI.jsonl").exists()


def test_text_report_names_units_and_counts(project, stub_providers, capsys):
    stub_providers(StubProvider())
    entrypoint.main(argv(project))
    out = capsys.readouterr().out

    assert "unit" in out and "kind" in out
    assert "jack" in out and "OpenAI" in out
    assert "ok" in out
    assert "units:" in out and "items:" in out


def test_json_report_is_machine_readable(project, stub_providers, capsys):
    stub_providers(StubProvider())
    entrypoint.main(argv(project, "--json"))
    payload = json.loads(capsys.readouterr().out)

    assert payload["accounts_ok"] == 2
    assert payload["tweets_fetched"] == 16
    assert payload["records_normalized"] == 16
    # 16 fetched, 8 distinct identities.
    assert payload["tweets_inserted"] == 8
    assert payload["tweets_duplicate"] == 8
    assert len(payload["outcomes"]) == 2


def test_second_run_reports_duplicates(project, stub_providers, capsys):
    stub_providers(StubProvider())
    entrypoint.main(argv(project))
    capsys.readouterr()  # discard the first run's text report
    entrypoint.main(argv(project, "--json"))
    payload = json.loads(capsys.readouterr().out)

    assert payload["tweets_inserted"] == 0
    assert payload["tweets_duplicate"] == 16


def test_account_filter_restricts_the_run(project, stub_providers, capsys):
    stub_providers(StubProvider())
    entrypoint.main(argv(project, "--account", "jack", "--json"))
    payload = json.loads(capsys.readouterr().out)

    assert payload["accounts_total"] == 1
    assert payload["outcomes"][0]["account"] == "jack"
    assert payload["outcomes"][0]["kind"] == "x"


def test_data_dir_override_wins(project, stub_providers, tmp_path):
    stub_providers(StubProvider())
    elsewhere = tmp_path / "elsewhere"
    entrypoint.main(argv(project, "--data-dir", str(elsewhere)))

    assert (elsewhere / "items" / "x.jsonl").exists()


def test_empty_timeline_run_is_still_successful(project, stub_providers, capsys):
    stub_providers(StubProvider(EMPTY_TIMELINE))
    assert entrypoint.main(argv(project, "--json")) == entrypoint.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["accounts_empty"] == 2
    assert payload["accounts_failed"] == 0


# --- RSS units ------------------------------------------------------------
def test_rss_source_runs_end_to_end(mixed_project, stub_providers, stub_rss_provider, capsys):
    stub_providers(StubProvider())
    stub_rss_provider(StubRssProvider())
    _, _, data_dir = mixed_project

    assert entrypoint.main(argv(mixed_project, "--json")) == entrypoint.EXIT_OK
    payload = json.loads(capsys.readouterr().out)

    outcome = next(o for o in payload["outcomes"] if o["kind"] == "rss")
    assert outcome["source_id"] == "vnovel"
    assert outcome["status"] == "ok"
    assert outcome["normalized"] > 0

    stored = data_dir / "items" / "vnovel.jsonl"
    assert stored.exists()
    assert len([line for line in stored.read_text(encoding="utf-8").splitlines() if line]) == (
        outcome["inserted"]
    )


def test_no_rss_flag_skips_rss_sources(mixed_project, stub_providers, stub_rss_provider, capsys):
    stub_providers(StubProvider())
    stub_rss_provider(StubRssProvider())
    entrypoint.main(argv(mixed_project, "--no-rss", "--json"))
    payload = json.loads(capsys.readouterr().out)

    assert payload["accounts_total"] == 1
    assert payload["outcomes"][0]["kind"] == "x"


def test_rss_and_x_units_can_run_in_one_pass(mixed_project, stub_providers, stub_rss_provider, capsys):
    """``python main.py`` dispatches both kinds — that is the Phase 2 goal."""
    stub_providers(StubProvider())
    stub_rss_provider(StubRssProvider())
    _, _, data_dir = mixed_project

    entrypoint.main(argv(mixed_project, "--json"))
    payload = json.loads(capsys.readouterr().out)

    kinds = {outcome["kind"] for outcome in payload["outcomes"]}
    assert kinds == {"x", "rss"}
    assert (data_dir / "items" / "x.jsonl").exists()
    assert (data_dir / "items" / "vnovel.jsonl").exists()


def test_no_units_at_all_is_cannot_start(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML.format(data_dir=tmp_path / "data"), encoding="utf-8")
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("accounts: []\n", encoding="utf-8")

    code = entrypoint.main(["--config", str(config_path), "--accounts", str(accounts_path)])
    assert code == entrypoint.EXIT_CANNOT_START


# --- RSS-only (no X at all) ------------------------------------------------
def test_rss_only_run_never_builds_an_x_provider(rss_only_project, stub_rss_provider, monkeypatch):
    """The point of the decoupling: an empty ``provider.order`` means no X chain.

    ``build_providers`` is left real and merely spied on, so this asserts the
    production behaviour rather than a stub's.
    """
    built: dict = {}
    real_build = entrypoint.build_providers

    def spy(config, client):
        providers = real_build(config, client)
        built["providers"] = providers
        return providers

    monkeypatch.setattr(entrypoint, "build_providers", spy)
    stub_rss_provider(StubRssProvider())

    assert entrypoint.main(argv(rss_only_project, "--json")) == entrypoint.EXIT_OK
    assert built["providers"] == []


def test_rss_only_run_is_end_to_end_and_incremental(
    rss_only_project, stub_rss_provider, capsys
):
    """Config -> main/Runner -> RSS Provider -> Parser -> Normalizer -> Storage."""
    stub_rss_provider(StubRssProvider())
    _, _, data_dir = rss_only_project

    assert entrypoint.main(argv(rss_only_project, "--json")) == entrypoint.EXIT_OK
    payload = json.loads(capsys.readouterr().out)

    # Exactly one unit ran, and it is a feed — no X account was invented.
    assert payload["accounts_total"] == 1
    assert [o["kind"] for o in payload["outcomes"]] == ["rss"]
    outcome = payload["outcomes"][0]
    assert outcome["source_id"] == "vnovel"
    assert outcome["status"] == "ok"
    assert outcome["provider"] == "rss"
    assert outcome["route"] == "feed"
    # The real feed: 24 records, one URL listed twice -> 23 distinct items.
    assert outcome["inserted"] == 23
    assert outcome["duplicates"] == 1

    stored = data_dir / "items" / "vnovel.jsonl"
    assert stored.exists()
    assert len([line for line in stored.read_text(encoding="utf-8").splitlines() if line]) == 23
    # Nothing X-shaped was created.
    assert not (data_dir / "items" / "x.jsonl").exists()
    assert not (data_dir / "accounts").exists()

    # Second run: every item is already known.
    assert entrypoint.main(argv(rss_only_project, "--json")) == entrypoint.EXIT_OK
    again = json.loads(capsys.readouterr().out)
    assert again["tweets_inserted"] == 0
    assert again["tweets_duplicate"] == 24


def test_rss_only_with_no_rss_flag_has_nothing_to_do(rss_only_project, stub_rss_provider):
    """``--no-rss`` on an RSS-only project removes the last input source."""
    stub_rss_provider(StubRssProvider())
    assert entrypoint.main(argv(rss_only_project, "--no-rss")) == entrypoint.EXIT_CANNOT_START


# --- failing runs (exit 1) ------------------------------------------------
def test_all_accounts_failing_returns_run_failed(project, stub_providers, capsys):
    from domain.errors import NetworkError

    stub_providers(StubProvider(error=NetworkError("HTTP 503")))
    assert entrypoint.main(argv(project, "--json")) == entrypoint.EXIT_RUN_FAILED
    payload = json.loads(capsys.readouterr().out)
    assert payload["accounts_failed"] == 2


def test_partial_failure_is_exit_zero_without_strict(project, monkeypatch):
    """One dead account should not mark the whole job red by default."""
    monkeypatch.setattr(entrypoint, "build_providers", lambda config, client: [PickyProvider()])
    assert entrypoint.main(argv(project)) == entrypoint.EXIT_OK


def test_partial_failure_is_exit_one_with_strict(project, monkeypatch):
    monkeypatch.setattr(entrypoint, "build_providers", lambda config, client: [PickyProvider()])
    assert entrypoint.main(argv(project, "--strict")) == entrypoint.EXIT_RUN_FAILED


# --- reporting helpers ----------------------------------------------------
def test_render_report_lists_failed_attempts():
    from app.runner import AccountOutcome
    from app.runner import RunSummary

    summary = RunSummary(run_id="r1", started_at="2024-09-11T19:31:00+00:00")
    summary.outcomes.append(
        AccountOutcome(
            account="jack", status="error", error_kind="all_providers_failed",
            error="all providers failed for @jack: nitter/html=network_error: HTTP 503",
        )
    )
    text = entrypoint.render_report(summary)
    assert "jack" in text
    assert "all_providers_failed" in text


def test_bindings_short_lists_provider_routes():
    class P(BaseProvider):
        name = "nitter"
        routes_ = ("rss", "html")

        def fetch(self, account, route):
            raise NotImplementedError

    assert entrypoint.bindings_short([P()]) == ["nitter/rss", "nitter/html"]


def test_main_module_does_not_read_ci_environment(monkeypatch, project, stub_providers):
    """Business logic must run identically on a laptop and in CI."""
    stub_providers(StubProvider())
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_WORKSPACE", "/nonexistent")
    assert entrypoint.main(argv(project)) == entrypoint.EXIT_OK
