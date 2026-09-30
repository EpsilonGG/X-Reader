"""YouTube input: provider, parser, normalizer and the end-to-end chain.

The Phase 4 acceptance path for the third input family. What matters here is not
that YouTube data parses — the parser tests cover that — but that YouTube enters
the *same* pipeline as X and RSS and needs nothing new downstream: one
``NormalizedItem``, one Storage, no ``YouTubeStorage``, no
``YouTube -> Telegram`` shortcut.

No test here touches the network. The provider is given a client that returns the
fixture document, which is exactly the seam the architecture was built around.
"""
from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from pathlib import Path

import pytest

import main as entrypoint
from app.runner import KIND_YOUTUBE
from app.runner import STATUS_ERROR
from app.runner import STATUS_NO_TWEETS
from app.runner import STATUS_OK
from app.runner import STATUS_SKIPPED
from app.runner import Runner
from config.loader import load_config
from domain.errors import NetworkError
from domain.models.account import Account
from domain.models.item import SOURCE_YOUTUBE
from domain.models.item import NormalizedItem
from infrastructure.http.response import RawResponse
from normalizers.youtube import YoutubeNormalizer
from parsers.youtube import YoutubeParser
from providers.base import BaseProvider
from providers.youtube import YoutubeProvider
from providers.youtube import feed_url_for
from storage.jsonl import JsonlStorage
from tests.conftest import YOUTUBE_CHANNEL_ID
from tests.conftest import YOUTUBE_OTHER_CHANNEL_ID
from tests.conftest import fixture_text

FEED = fixture_text("youtube_channel_feed.xml")
OTHER_FEED = fixture_text("youtube_channel_feed_other.xml")
FETCHED = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

YOUTUBE_NAMESPACE = f"{SOURCE_YOUTUBE}:{YOUTUBE_CHANNEL_ID}"
OTHER_NAMESPACE = f"{SOURCE_YOUTUBE}:{YOUTUBE_OTHER_CHANNEL_ID}"


def atom_response(text: str, url: str) -> RawResponse:
    return RawResponse(
        url=url,
        status_code=200,
        content_type="application/atom+xml",
        text=text,
        fetched_at=FETCHED,
    )


class StubClient:
    """A client that serves fixture bodies instead of talking to the network."""

    def __init__(self, bodies: dict[str, str] | None = None) -> None:
        self.bodies = bodies if bodies is not None else {feed_url_for(YOUTUBE_CHANNEL_ID): FEED}
        self.calls: list[str] = []

    def get(self, url: str) -> RawResponse:
        self.calls.append(url)
        body = self.bodies.get(url)
        if body is None:
            raise NetworkError(f"HTTP 503 — {url}")
        return atom_response(body, url)

    def close(self) -> None:
        pass


class StubYoutubeProvider(BaseProvider):
    """A provider that returns the fixture, so ``main()`` needs no network."""

    name = "youtube"
    routes_ = ("feed",)

    def __init__(self, bodies: dict[str, str] | None = None) -> None:
        self.bodies = bodies or {
            YOUTUBE_CHANNEL_ID: FEED,
            YOUTUBE_OTHER_CHANNEL_ID: OTHER_FEED,
        }
        self.calls: list[str] = []

    def fetch(self, account: Account, route: str) -> RawResponse:
        self.calls.append(account.username)
        body = self.bodies.get(account.username)
        if body is None:
            raise NetworkError(f"HTTP 503 — channel {account.username}")
        return atom_response(body, feed_url_for(account.username))


def youtube_config(**overrides):
    data = {
        "provider": {"order": []},
        "storage": {"store_raw_response": "never"},
        "youtube_channels": [{"channel_id": YOUTUBE_CHANNEL_ID}],
    }
    data.update(overrides)
    from config.schema import Config

    return Config(**data)


# --- the chain reaches storage --------------------------------------------
def test_youtube_chain_reaches_storage(tmp_path):
    storage = JsonlStorage(data_dir=tmp_path / "data")
    provider = YoutubeProvider(
        youtube_config().enabled_youtube_channels, StubClient()
    )

    response = provider.fetch(Account(username=YOUTUBE_CHANNEL_ID), "feed")
    raw = YoutubeParser().parse(response, Account(username=YOUTUBE_CHANNEL_ID))
    items = YoutubeNormalizer().normalize(raw)
    report = storage.save(items)

    assert len(raw) == 3
    assert len(items) == 3
    assert report.inserted == 3
    assert report.source_id == YOUTUBE_NAMESPACE
    assert storage.known_keys(YOUTUBE_NAMESPACE) == {item.identity_key for item in items}


def test_stored_items_are_plain_normalized_items(tmp_path):
    storage = JsonlStorage(data_dir=tmp_path / "data")
    raw = YoutubeParser().parse(atom_response(FEED, feed_url_for(YOUTUBE_CHANNEL_ID)), Account(username=YOUTUBE_CHANNEL_ID))
    storage.save(YoutubeNormalizer().normalize(raw))

    reloaded = storage.load_items()
    assert len(reloaded) == 3
    assert all(type(item) is NormalizedItem for item in reloaded)
    assert all(item.source_id == YOUTUBE_NAMESPACE for item in reloaded)
    assert all(item.is_valid() for item in reloaded)


def test_the_store_file_is_named_after_the_namespace(tmp_path):
    storage = JsonlStorage(data_dir=tmp_path / "data")
    raw = YoutubeParser().parse(atom_response(FEED, feed_url_for(YOUTUBE_CHANNEL_ID)), Account(username=YOUTUBE_CHANNEL_ID))
    storage.save(YoutubeNormalizer().normalize(raw))

    # ":" is not filesystem-safe, so it becomes "_" — the identity is unchanged.
    assert storage.item_path(YOUTUBE_NAMESPACE).name == f"youtube_{YOUTUBE_CHANNEL_ID}.jsonl"
    assert storage.item_path(YOUTUBE_NAMESPACE).exists()


def test_the_chain_is_incremental(tmp_path):
    storage = JsonlStorage(data_dir=tmp_path / "data")
    raw = YoutubeParser().parse(atom_response(FEED, feed_url_for(YOUTUBE_CHANNEL_ID)), Account(username=YOUTUBE_CHANNEL_ID))
    items = YoutubeNormalizer().normalize(raw)

    first = storage.save(items)
    second = storage.save(items)

    assert first.inserted == 3
    assert second.inserted == 0
    assert second.duplicates == 3


def test_youtube_items_do_not_collide_with_x_items(tmp_path):
    """``x:12345`` and ``youtube:UC...:12345`` are different things."""
    storage = JsonlStorage(data_dir=tmp_path / "data")
    raw = YoutubeParser().parse(atom_response(FEED, feed_url_for(YOUTUBE_CHANNEL_ID)), Account(username=YOUTUBE_CHANNEL_ID))
    storage.save(YoutubeNormalizer().normalize(raw))

    clash = NormalizedItem(
        source_id="x",
        item_id="dQw4w9WgXcQ",
        content="a tweet that happens to share the id",
        fetched_at=FETCHED.isoformat(),
    )
    report = storage.save([clash])

    assert report.inserted == 1
    assert clash.identity_key in storage.known_keys("x")
    assert clash.identity_key not in storage.known_keys(YOUTUBE_NAMESPACE)


# --- multi-channel isolation ----------------------------------------------
def test_two_channels_land_in_two_files(tmp_path):
    storage = JsonlStorage(data_dir=tmp_path / "data")
    config = youtube_config(
        youtube_channels=[
            {"channel_id": YOUTUBE_CHANNEL_ID},
            {"channel_id": YOUTUBE_OTHER_CHANNEL_ID},
        ]
    )
    provider = YoutubeProvider(
        config.enabled_youtube_channels,
        StubClient(
            {
                feed_url_for(YOUTUBE_CHANNEL_ID): FEED,
                feed_url_for(YOUTUBE_OTHER_CHANNEL_ID): OTHER_FEED,
            }
        ),
    )

    for channel_id in (YOUTUBE_CHANNEL_ID, YOUTUBE_OTHER_CHANNEL_ID):
        unit = Account(username=channel_id)
        raw = YoutubeParser().parse(provider.fetch(unit, "feed"), unit)
        storage.save(YoutubeNormalizer().normalize(raw))

    first = storage.known_keys(YOUTUBE_NAMESPACE)
    second = storage.known_keys(OTHER_NAMESPACE)

    assert len(first) == 3
    assert len(second) == 1
    assert first.isdisjoint(second)
    assert storage.item_path(YOUTUBE_NAMESPACE) != storage.item_path(OTHER_NAMESPACE)


# --- the runner -----------------------------------------------------------
def runner_for(config, provider, tmp_path, **overrides) -> Runner:
    storage = JsonlStorage(data_dir=tmp_path / "data")
    kwargs = dict(
        config=config,
        accounts=[],
        providers=[],
        storage=storage,
        youtube_channels=config.enabled_youtube_channels,
        youtube_provider=provider,
        reader=storage,
    )
    kwargs.update(overrides)
    return Runner(**kwargs)


def test_runner_processes_a_youtube_channel(tmp_path):
    config = youtube_config()
    provider = StubYoutubeProvider()
    summary = runner_for(config, provider, tmp_path).run()

    assert [o.kind for o in summary.outcomes] == [KIND_YOUTUBE]
    assert summary.outcomes[0].status == STATUS_OK
    assert summary.outcomes[0].inserted == 3
    assert provider.calls == [YOUTUBE_CHANNEL_ID]


def test_runner_reports_the_stored_namespace_as_the_source_id(tmp_path):
    """The run record and the stored items must agree about identity."""
    config = youtube_config()
    summary = runner_for(config, StubYoutubeProvider(), tmp_path).run()
    assert summary.outcomes[0].source_id == YOUTUBE_NAMESPACE


def test_runner_records_the_youtube_kind_in_the_audit_trail(tmp_path):
    config = youtube_config()
    summary = runner_for(config, StubYoutubeProvider(), tmp_path).run()
    storage = JsonlStorage(data_dir=tmp_path / "data")
    run_lines = list((storage.runs_dir).glob("*.jsonl"))
    assert run_lines
    records = [
        json.loads(line)
        for line in run_lines[0].read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [record["kind"] for record in records] == [KIND_YOUTUBE]
    assert records[0]["source_id"] == YOUTUBE_NAMESPACE


def test_runner_skips_a_disabled_channel(tmp_path):
    """The runner's own skip branch, for a caller that passes the full list.

    ``main.py`` filters with ``enabled_youtube_channels`` first, so this path is
    normally unreachable — but the runner must still be honest about a channel
    it was told to skip rather than silently dropping it from the report.
    """
    config = youtube_config(
        youtube_channels=[
            {"channel_id": YOUTUBE_CHANNEL_ID, "enabled": False},
            {"channel_id": YOUTUBE_OTHER_CHANNEL_ID},
        ]
    )
    provider = StubYoutubeProvider()
    summary = runner_for(
        config, provider, tmp_path, youtube_channels=config.youtube_channels
    ).run()

    assert [o.status for o in summary.outcomes] == [STATUS_SKIPPED, STATUS_OK]
    # The disabled channel was never fetched.
    assert provider.calls == [YOUTUBE_OTHER_CHANNEL_ID]


def test_a_disabled_channel_is_filtered_out_before_the_runner(tmp_path):
    """What actually happens in a run: a disabled channel is not a unit at all."""
    config = youtube_config(
        youtube_channels=[
            {"channel_id": YOUTUBE_CHANNEL_ID, "enabled": False},
            {"channel_id": YOUTUBE_OTHER_CHANNEL_ID},
        ]
    )
    provider = StubYoutubeProvider()
    summary = runner_for(config, provider, tmp_path).run()

    assert [o.status for o in summary.outcomes] == [STATUS_OK]
    assert provider.calls == [YOUTUBE_OTHER_CHANNEL_ID]


def test_one_channel_failing_does_not_stop_the_other(tmp_path):
    """Per-unit isolation, the same guarantee X and RSS already have."""
    config = youtube_config(
        youtube_channels=[
            {"channel_id": YOUTUBE_CHANNEL_ID},
            {"channel_id": YOUTUBE_OTHER_CHANNEL_ID},
        ]
    )
    # Only the second channel has a body, so the first raises NetworkError.
    provider = StubYoutubeProvider({YOUTUBE_OTHER_CHANNEL_ID: OTHER_FEED})
    summary = runner_for(config, provider, tmp_path).run()

    assert [o.status for o in summary.outcomes] == [STATUS_ERROR, STATUS_OK]
    # The unit-level verdict is "every route failed"; the underlying cause is
    # still recorded per attempt, which is where an operator looks first.
    assert summary.outcomes[0].error_kind == "all_providers_failed"
    assert summary.outcomes[0].attempts[0]["error_kind"] == "network_error"
    assert summary.outcomes[1].inserted == 1
    assert summary.accounts_failed == 1


def test_a_channel_with_no_videos_is_an_empty_success_not_a_failure(tmp_path):
    empty_feed = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns:media="http://search.yahoo.com/mrss/" '
        'xmlns="http://www.w3.org/2005/Atom">'
        f"<yt:channelId>{YOUTUBE_CHANNEL_ID}</yt:channelId>"
        "<title>Quiet Channel</title>"
        "</feed>"
    )
    config = youtube_config()
    provider = StubYoutubeProvider({YOUTUBE_CHANNEL_ID: empty_feed})
    summary = runner_for(config, provider, tmp_path).run()

    assert summary.outcomes[0].status == STATUS_NO_TWEETS
    assert summary.accounts_failed == 0


def test_no_youtube_provider_reports_a_failure_rather_than_silence(tmp_path):
    """A wiring bug must be visible, not an empty successful run."""
    config = youtube_config()
    summary = runner_for(config, None, tmp_path).run()
    assert summary.outcomes[0].status == STATUS_ERROR


def test_youtube_and_rss_and_x_share_one_store(tmp_path):
    """Three families, one Storage — the property the freeze is really about."""
    from config.schema import Config
    from normalizers.rss import RssNormalizer
    from parsers.rss import RssParser

    config = Config(
        provider={"order": []},
        storage={"store_raw_response": "never"},
        rss_sources=[{"id": "vnovel", "url": "https://v.example/rss.xml"}],
        youtube_channels=[{"channel_id": YOUTUBE_CHANNEL_ID}],
    )
    storage = JsonlStorage(data_dir=tmp_path / "data")

    vnovel = fixture_text("vnovel_rss_sample.xml")
    rss_raw = RssParser().parse(
        RawResponse(
            url="https://v.example/rss.xml",
            status_code=200,
            content_type="application/rss+xml",
            text=vnovel,
            fetched_at=FETCHED,
        ),
        Account(username="vnovel"),
    )
    rss_items = RssNormalizer().normalize(rss_raw)
    yt_raw = YoutubeParser().parse(
        atom_response(FEED, feed_url_for(YOUTUBE_CHANNEL_ID)), Account(username=YOUTUBE_CHANNEL_ID)
    )
    yt_items = YoutubeNormalizer().normalize(yt_raw)

    storage.save(rss_items)
    storage.save(yt_items)

    assert storage.known_keys("vnovel")
    assert storage.known_keys(YOUTUBE_NAMESPACE)
    # No key is shared between the two families.
    assert storage.known_keys("vnovel").isdisjoint(storage.known_keys(YOUTUBE_NAMESPACE))
    # And one read-back call sees both. The real VNovel feed lists one URL twice,
    # so its 24 normalized items are 23 distinct identities in the store.
    assert len(storage.load_items()) == 23 + len(yt_items)


def test_youtube_is_not_written_to_the_raw_tweet_archive(tmp_path):
    """The Phase 1 archive is X-only evidence; YouTube has no frozen raw shape."""
    config = youtube_config()
    runner_for(config, StubYoutubeProvider(), tmp_path).run()
    storage = JsonlStorage(data_dir=tmp_path / "data")
    assert not storage.accounts_dir.exists() or not list(storage.accounts_dir.glob("*.jsonl"))


# --- entrypoint -----------------------------------------------------------
YOUTUBE_ONLY_CONFIG = """
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
youtube_channels:
  - channel_id: {channel_id}
"""


@pytest.fixture
def youtube_only_project(tmp_path) -> tuple[Path, Path, Path]:
    """No X, no RSS — one YouTube channel is the only input."""
    data_dir = tmp_path / "data"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        YOUTUBE_ONLY_CONFIG.format(data_dir=data_dir, channel_id=YOUTUBE_CHANNEL_ID),
        encoding="utf-8",
    )
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("accounts: []\n", encoding="utf-8")
    return config_path, accounts_path, data_dir


@pytest.fixture
def stub_youtube_provider(monkeypatch):
    def _install(provider):
        monkeypatch.setattr(
            entrypoint, "build_youtube_provider", lambda config, client: provider
        )

    return _install


def test_youtube_only_config_loads(youtube_only_project):
    config_path, _, _ = youtube_only_project
    config = load_config(config_path)
    assert config.x_input_enabled is False
    assert config.enabled_rss_sources == []
    assert config.youtube_input_enabled is True


def test_youtube_only_run_is_end_to_end(
    youtube_only_project, stub_youtube_provider, capsys
):
    config_path, accounts_path, data_dir = youtube_only_project
    provider = StubYoutubeProvider()
    stub_youtube_provider(provider)

    code = entrypoint.main(
        ["--config", str(config_path), "--accounts", str(accounts_path), "--json"]
    )
    assert code == entrypoint.EXIT_OK
    assert provider.calls == [YOUTUBE_CHANNEL_ID]

    report = json.loads(capsys.readouterr().out)
    assert report["tweets_inserted"] == 3
    assert [o["kind"] for o in report["outcomes"]] == [KIND_YOUTUBE]
    assert report["outcomes"][0]["source_id"] == YOUTUBE_NAMESPACE

    stored = JsonlStorage(data_dir=data_dir).known_keys(YOUTUBE_NAMESPACE)
    assert len(stored) == 3


def test_a_second_youtube_run_adds_nothing(
    youtube_only_project, stub_youtube_provider, capsys
):
    config_path, accounts_path, data_dir = youtube_only_project
    stub_youtube_provider(StubYoutubeProvider())
    args = ["--config", str(config_path), "--accounts", str(accounts_path), "--json"]

    assert entrypoint.main(args) == entrypoint.EXIT_OK
    capsys.readouterr()
    assert entrypoint.main(args) == entrypoint.EXIT_OK

    report = json.loads(capsys.readouterr().out)
    assert report["tweets_inserted"] == 0
    assert report["tweets_duplicate"] == 3
    assert len(JsonlStorage(data_dir=data_dir).known_keys(YOUTUBE_NAMESPACE)) == 3


def test_no_youtube_flag_leaves_nothing_to_do(
    youtube_only_project, stub_youtube_provider
):
    """The mirror of ``--no-rss``: skipping the only input is a start-up error."""
    config_path, accounts_path, _ = youtube_only_project
    stub_youtube_provider(StubYoutubeProvider())
    code = entrypoint.main(
        ["--config", str(config_path), "--accounts", str(accounts_path), "--no-youtube"]
    )
    assert code == entrypoint.EXIT_CANNOT_START
