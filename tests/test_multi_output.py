"""Multi-output delivery: the Phase 4 acceptance matrix.

The property this file exists to prove is the one Phase 4 was really about:
**a second output changes nothing upstream**. Adding QQ required no change to
``NormalizedItem``, Storage, the Runner or the Telegram adapter — and the two
outputs keep entirely separate delivery state, so one can succeed while the other
fails, retries independently, and never re-sends what already arrived.

Every case here runs the real ``Runner`` against real ``JsonlStorage`` on disk,
with real adapters over ``httpx.MockTransport`` and stub providers (no network).
The seven combinations of {X, RSS, YouTube} x {Telegram, QQ} are covered, plus
the partial-failure and retry behaviour that the delivery design exists for.
"""
from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone

import httpx
import pytest

from app.runner import Runner
from config.schema import Config
from config.schema import RssSourceConfig
from domain.models.account import Account
from infrastructure.http.response import RawResponse
from outputs.qq import QQOutput
from outputs.telegram import TelegramOutput
from providers.base import BaseProvider
from storage.jsonl import JsonlStorage
from tests.conftest import YOUTUBE_CHANNEL_ID
from tests.conftest import fixture_text
from tests.conftest import youtube_feed_url

FETCHED = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

TIMELINE = fixture_text("nitter_timeline.html")
VNOVEL_RSS = fixture_text("vnovel_rss_sample.xml")
YOUTUBE_FEED = fixture_text("youtube_channel_feed.xml")

TG_API = "http://telegram.test"
QQ_API = "http://onebot.test:3000"
TG_TOKEN = "fake-tg-token"
QQ_TOKEN = "fake-onebot-token"


# --------------------------------------------------------------------------
# stub providers — no network anywhere in this file
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


class StubYoutubeProvider(BaseProvider):
    name = "youtube"
    routes_ = ("feed",)

    def fetch(self, account: Account, route: str) -> RawResponse:
        return RawResponse(
            url=youtube_feed_url(account.username),
            status_code=200,
            content_type="application/atom+xml",
            text=YOUTUBE_FEED,
            fetched_at=FETCHED,
        )


# --------------------------------------------------------------------------
# recorders
# --------------------------------------------------------------------------
class Recorder:
    """A scripted endpoint: records requests, replies from a queue.

    ``dead=True`` makes every request raise ``ConnectError``, which is how an
    unreachable Telegram API or a stopped OneBot is simulated. That is the case
    the whole retry design exists for, so it must be exercised through the real
    adapter rather than stubbed at the adapter boundary.
    """

    def __init__(self, *replies, dead: bool = False, fail_status: int | None = None) -> None:
        self.replies = list(replies)
        self.dead = dead
        self.fail_status = fail_status
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append({"url": str(request.url), "body": body})
        if self.dead:
            raise httpx.ConnectError("connection refused")
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        if self.fail_status is not None:
            return httpx.Response(self.fail_status, json={"status": "failed", "retcode": 1})
        return httpx.Response(200, json={"ok": True, "status": "ok", "retcode": 0})

    @property
    def count(self) -> int:
        return len(self.requests)


def telegram(recorder: Recorder) -> TelegramOutput:
    return TelegramOutput(
        bot_token=TG_TOKEN,
        chat_id="-100123",
        api_base=TG_API,
        transport=httpx.MockTransport(recorder),
    )


def qq(recorder: Recorder) -> QQOutput:
    return QQOutput(
        api_base=QQ_API,
        group_id="123456789",
        access_token=QQ_TOKEN,
        transport=httpx.MockTransport(recorder),
    )


# --------------------------------------------------------------------------
# runner harness
# --------------------------------------------------------------------------
def base_config(**overrides) -> Config:
    data = {
        "provider": {"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}},
        "storage": {"store_raw_response": "never"},
    }
    data.update(overrides)
    return Config(**data)


def run_once(
    data_dir,
    *,
    with_x: bool = False,
    with_rss: bool = False,
    with_youtube: bool = False,
    outputs: list | None = None,
    refetch: bool = True,
) -> tuple:
    """One full run. ``refetch=False`` builds a Runner with no providers at all."""
    config = base_config(
        rss_sources=[{"id": "vnovel", "url": "https://v.example/rss.xml"}] if with_rss else [],
        youtube_channels=[{"channel_id": YOUTUBE_CHANNEL_ID}] if with_youtube else [],
    )
    storage = JsonlStorage(data_dir=data_dir)
    runner = Runner(
        config=config,
        accounts=[Account(username="jack")] if with_x and refetch else [],
        providers=[StubXProvider()] if with_x and refetch else [],
        storage=storage,
        rss_sources=config.enabled_rss_sources if (with_rss and refetch) else [],
        rss_provider=StubRssProvider() if with_rss and refetch else None,
        youtube_channels=config.enabled_youtube_channels if (with_youtube and refetch) else [],
        youtube_provider=StubYoutubeProvider() if with_youtube and refetch else None,
        outputs=outputs or [],
        reader=storage,
    )
    return runner.run(), storage


def state_of(data_dir, output: str) -> dict[str, str]:
    records, _ = _read(data_dir / "delivery" / f"{output}.jsonl")
    state: dict[str, str] = {}
    for record in records:
        state[record["identity_key"]] = record["status"]
    return state


def _read(path):
    if not path.exists():
        return [], 0
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            records.append(json.loads(line))
    return records, 0


# --------------------------------------------------------------------------
# the seven combinations
# --------------------------------------------------------------------------
def test_x_to_telegram(tmp_path):
    tg = Recorder()
    summary, _ = run_once(tmp_path, with_x=True, outputs=[telegram(tg)])
    assert summary.outcomes[0].kind == "x"
    assert summary.delivered == tg.count > 0
    assert summary.deliveries_ok


def test_rss_to_telegram(tmp_path):
    tg = Recorder()
    summary, _ = run_once(tmp_path, with_rss=True, outputs=[telegram(tg)])
    assert summary.outcomes[0].kind == "rss"
    assert summary.delivered == tg.count > 0


def test_youtube_to_telegram(tmp_path):
    tg = Recorder()
    summary, _ = run_once(tmp_path, with_youtube=True, outputs=[telegram(tg)])
    assert summary.outcomes[0].kind == "youtube"
    assert tg.count == 3
    assert summary.delivered == 3


def test_youtube_to_qq(tmp_path):
    onebot = Recorder()
    summary, _ = run_once(tmp_path, with_youtube=True, outputs=[qq(onebot)])
    assert onebot.count == 3
    assert summary.delivered == 3
    assert summary.deliveries[0].output == "qq"


def test_x_to_telegram_and_qq(tmp_path):
    tg, onebot = Recorder(), Recorder()
    summary, _ = run_once(tmp_path, with_x=True, outputs=[telegram(tg), qq(onebot)])
    assert [d.output for d in summary.deliveries] == ["telegram", "qq"]
    assert tg.count == onebot.count > 0
    assert summary.delivered == tg.count + onebot.count


def test_rss_to_telegram_and_qq(tmp_path):
    tg, onebot = Recorder(), Recorder()
    summary, _ = run_once(tmp_path, with_rss=True, outputs=[telegram(tg), qq(onebot)])
    assert tg.count == onebot.count > 0
    assert summary.deliveries_ok


def test_youtube_to_telegram_and_qq(tmp_path):
    tg, onebot = Recorder(), Recorder()
    summary, _ = run_once(tmp_path, with_youtube=True, outputs=[telegram(tg), qq(onebot)])
    assert tg.count == 3
    assert onebot.count == 3
    assert summary.delivered == 6
    # Two outputs, two independent files, same three identities in each.
    assert set(state_of(tmp_path, "telegram")) == set(state_of(tmp_path, "qq"))


def test_all_three_inputs_to_both_outputs(tmp_path):
    """The full target architecture: three inputs, two outputs, one store."""
    tg, onebot = Recorder(), Recorder()
    summary, storage = run_once(
        tmp_path, with_x=True, with_rss=True, with_youtube=True,
        outputs=[telegram(tg), qq(onebot)],
    )
    kinds = [o.kind for o in summary.outcomes]
    assert kinds == ["x", "rss", "youtube"]
    assert all(o.status == "ok" for o in summary.outcomes)
    # Every stored identity was offered to both outputs.
    keys = storage.known_keys()
    assert set(state_of(tmp_path, "telegram")) == keys
    assert set(state_of(tmp_path, "qq")) == keys
    assert summary.delivery_failed == 0


# --------------------------------------------------------------------------
# one output failing must not touch the other
# --------------------------------------------------------------------------
def test_a_dead_qq_does_not_stop_telegram(tmp_path):
    tg, onebot = Recorder(), Recorder(dead=True)
    summary, storage = run_once(
        tmp_path, with_youtube=True, outputs=[telegram(tg), qq(onebot)]
    )

    assert summary.delivered == 3
    assert summary.delivery_failed == 3
    assert not summary.deliveries_ok
    # The item store is untouched by a delivery failure.
    assert len(storage.known_keys()) == 3
    assert set(state_of(tmp_path, "telegram").values()) == {"sent"}
    assert set(state_of(tmp_path, "qq").values()) == {"failed"}


def test_a_dead_telegram_does_not_stop_qq(tmp_path):
    tg, onebot = Recorder(dead=True), Recorder()
    summary, _ = run_once(
        tmp_path, with_youtube=True, outputs=[telegram(tg), qq(onebot)]
    )
    assert onebot.count == 3
    assert summary.delivered == 3
    assert summary.delivery_failed == 3


def test_delivery_failure_does_not_unsee_the_items(tmp_path):
    """A failed delivery must never make an item look new again."""
    tg, onebot = Recorder(), Recorder(dead=True)
    _, storage = run_once(tmp_path, with_youtube=True, outputs=[telegram(tg), qq(onebot)])
    assert len(storage.known_keys()) == 3

    summary, _ = run_once(
        tmp_path, with_youtube=True, outputs=[telegram(Recorder()), qq(Recorder(dead=True))]
    )
    assert summary.inserted == 0
    assert summary.duplicates == 3


# --------------------------------------------------------------------------
# per-output delivery state and retry
# --------------------------------------------------------------------------
def test_telegram_sent_and_qq_failed_then_telegram_skips_and_qq_retries(tmp_path):
    """The scenario the whole delivery design exists for.

    Run 1: Telegram succeeds, QQ is unreachable. Run 2: Telegram must send
    *nothing* (its items are already sent) while QQ retries exactly the items it
    failed on. Two outputs, two states, one identity.
    """
    tg1, onebot1 = Recorder(), Recorder(dead=True)
    summary1, _ = run_once(
        tmp_path, with_youtube=True, outputs=[telegram(tg1), qq(onebot1)]
    )
    assert summary1.delivered == 3
    assert summary1.delivery_failed == 3

    # Run 2: Telegram is healthy but must stay silent; QQ is healthy again.
    tg2, onebot2 = Recorder(), Recorder()
    summary2, _ = run_once(
        tmp_path, with_youtube=True, outputs=[telegram(tg2), qq(onebot2)]
    )

    assert tg2.count == 0, "Telegram re-sent items it had already delivered"
    assert onebot2.count == 3, "QQ did not retry the items it had failed on"
    assert summary2.delivered == 3
    assert summary2.deliveries[0].pending == 0
    assert summary2.deliveries[1].pending == 3
    assert set(state_of(tmp_path, "qq").values()) == {"sent"}


def test_a_successful_qq_delivery_is_never_re_sent(tmp_path):
    tg, onebot = Recorder(), Recorder()
    run_once(tmp_path, with_youtube=True, outputs=[telegram(tg), qq(onebot)])

    tg2, onebot2 = Recorder(), Recorder()
    summary, _ = run_once(
        tmp_path, with_youtube=True, outputs=[telegram(tg2), qq(onebot2)]
    )
    assert tg2.count == 0
    assert onebot2.count == 0
    assert summary.delivered == 0


def test_only_the_failed_qq_items_are_retried(tmp_path):
    """Partial failure inside one output: the successes must not be re-sent."""
    # Three items; the middle one is rejected by OneBot (retcode != 0).
    onebot = Recorder(
        httpx.Response(200, json={"status": "ok", "retcode": 0}),
        httpx.Response(200, json={"status": "failed", "retcode": 100, "msg": "bad"}),
        httpx.Response(200, json={"status": "ok", "retcode": 0}),
    )
    summary1, _ = run_once(tmp_path, with_youtube=True, outputs=[qq(onebot)])
    assert summary1.delivered == 2
    assert summary1.delivery_failed == 1

    onebot2 = Recorder()
    summary2, _ = run_once(tmp_path, with_youtube=True, outputs=[qq(onebot2)])
    assert onebot2.count == 1, "QQ retried an item it had already delivered"
    assert summary2.delivered == 1
    assert summary2.deliveries[0].pending == 1


def test_the_retry_needs_no_refetch(tmp_path):
    """Delivery is retried from storage, not by fetching again."""
    onebot = Recorder(dead=True)
    run_once(tmp_path, with_youtube=True, outputs=[qq(onebot)])

    # No providers, no accounts, no sources: nothing can be fetched this run.
    onebot2 = Recorder()
    summary, storage = run_once(
        tmp_path, with_youtube=False, outputs=[qq(onebot2)], refetch=False
    )

    assert summary.outcomes == []
    assert onebot2.count == 3, "the pending items were not re-delivered from storage"
    assert summary.delivered == 3
    assert set(state_of(tmp_path, "qq").values()) == {"sent"}


def test_the_two_output_states_live_in_separate_files(tmp_path):
    tg, onebot = Recorder(), Recorder(dead=True)
    run_once(tmp_path, with_youtube=True, outputs=[telegram(tg), qq(onebot)])

    assert (tmp_path / "delivery" / "telegram.jsonl").exists()
    assert (tmp_path / "delivery" / "qq.jsonl").exists()
    assert state_of(tmp_path, "telegram") != state_of(tmp_path, "qq")


def test_one_output_does_not_read_the_others_state(tmp_path):
    """QQ's failure must not make Telegram think its items are pending."""
    tg, onebot = Recorder(), Recorder(dead=True)
    run_once(tmp_path, with_youtube=True, outputs=[telegram(tg), qq(onebot)])

    tg2 = Recorder()
    summary, _ = run_once(tmp_path, with_youtube=True, outputs=[telegram(tg2)])
    assert tg2.count == 0
    assert summary.deliveries[0].pending == 0


def test_a_delivery_failure_does_not_change_the_exit_contract(tmp_path):
    """Acquisition succeeded; a QQ outage must not turn the run into a failure."""
    tg, onebot = Recorder(), Recorder(dead=True)
    summary, _ = run_once(
        tmp_path, with_youtube=True, outputs=[telegram(tg), qq(onebot)]
    )
    assert summary.accounts_failed == 0
    assert summary.deliveries_ok is False


def test_an_output_can_be_added_after_items_already_exist(tmp_path):
    """Enabling QQ later must deliver the existing backlog, not just new items."""
    run_once(tmp_path, with_youtube=True, outputs=[telegram(Recorder())])

    onebot = Recorder()
    summary, _ = run_once(tmp_path, with_youtube=True, outputs=[qq(onebot)])
    assert onebot.count == 3
    assert summary.delivered == 3
    assert summary.inserted == 0
