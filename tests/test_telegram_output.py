"""Telegram output adapter tests (task section 18, cases A-J).

The adapter is the first real ``OutputAdapter``, so these tests are where the
delivery contract is actually pinned down. They are unit tests: the Bot API is
replaced by an ``httpx.MockTransport``, which is the only honest way to exercise
status-code mapping, 429 retry and message splitting — none of those can be
provoked against the live API on demand.

What is *not* here: anything about where items came from. This file never
mentions a provider, a parser or a normalizer, because the adapter never does.
Delivery persistence is checked against real storage in
``tests/test_delivery_integration.py``.
"""
from __future__ import annotations

import json

import httpx
import pytest

from domain.models.item import PRECISION_SECOND
from domain.models.item import SOURCE_X
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from outputs.telegram import MESSAGE_LIMIT
from outputs.telegram import TelegramOutput
from outputs.telegram import _utf16_len
from outputs.telegram import escape
from outputs.telegram import resolve_credentials

TOKEN = "fake-token"
CHAT_ID = "-1001234567890"
API = "https://api.telegram.test"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def make_item(
    item_id: str = "1",
    *,
    source_id: str = SOURCE_X,
    title: str | None = "A title",
    content: str | None = "hello world",
    publisher: str | None = "jack",
    canonical_url: str | None = None,
    published_at: str | None = "2024-09-11T19:31:00+00:00",
    published_at_raw: str = "Sep 11, 2024 · 7:31 PM UTC",
    media: list[MediaItem] | None = None,
) -> NormalizedItem:
    return NormalizedItem(
        source_id=source_id,
        item_id=item_id,
        title=title,
        content=content,
        publisher=publisher,
        # ``None`` means "use the default"; ``""`` is an explicit empty value and
        # must survive, or a test cannot construct an item with no link.
        canonical_url=(
            f"https://x.com/jack/status/{item_id}" if canonical_url is None else canonical_url
        ),
        published_at=published_at,
        published_at_raw=published_at_raw,
        published_precision=PRECISION_SECOND,
        fetched_at="2024-09-11T19:31:05+00:00",
        media=list(media or []),
    )


class Recorder:
    """A scripted Bot API: records every request, replies from a queue."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append(
            {"url": str(request.url), "method": request.method, "body": body}
        )
        reply = self.replies.pop(0) if self.replies else _ok()
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def texts(self) -> list[str]:
        return [r["body"].get("text", "") for r in self.requests]


def _ok() -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})


def _error(status: int, description: str, *, retry_after: float | None = None):
    body: dict = {"ok": False, "error_code": status, "description": description}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return httpx.Response(status, json=body)


def make_output(recorder: Recorder, *, token: str = TOKEN, chat_id: str = CHAT_ID, **kwargs):
    """An adapter wired to a scripted transport, with sleep recorded not slept."""
    slept: list[float] = []
    output = TelegramOutput(
        bot_token=token,
        chat_id=chat_id,
        api_base=API,
        transport=httpx.MockTransport(recorder),
        sleep=slept.append,
        **kwargs,
    )
    output.slept = slept  # type: ignore[attr-defined]
    return output


# --------------------------------------------------------------------------
# H. message length handling
# --------------------------------------------------------------------------
def test_a_short_message_is_a_single_part():
    output = make_output(Recorder())
    assert output.split("hello") == ["hello"]


def test_a_message_at_the_boundary_is_not_split():
    output = make_output(Recorder())
    exact = "a" * MESSAGE_LIMIT
    assert output.split(exact) == [exact]


def test_one_character_over_the_boundary_is_split():
    output = make_output(Recorder())
    parts = output.split("a" * (MESSAGE_LIMIT + 1))
    assert len(parts) == 2
    assert "".join(parts) == "a" * (MESSAGE_LIMIT + 1)


def test_a_long_item_is_split_and_every_part_is_within_the_limit():
    output = make_output(Recorder())
    item = make_item(content="x" * (MESSAGE_LIMIT * 3))
    parts = output.split(output.render(item))
    assert len(parts) > 1
    for part in parts:
        assert _utf16_len(part) <= MESSAGE_LIMIT


def test_the_limit_is_counted_in_utf16_units_not_code_points():
    """A message that *looks* short can still be over the limit.

    Telegram counts UTF-16 code units. An emoji is one code point but two units,
    so splitting on ``len()`` would produce a part the API rejects. This project
    is largely Japanese content, where the gap is real.
    """
    output = make_output(Recorder())
    emoji = "\U0001f600"  # 1 code point, 2 UTF-16 units
    assert _utf16_len(emoji) == 2
    assert len(emoji) == 1

    text = emoji * (MESSAGE_LIMIT // 2 + 1)
    assert len(text) < MESSAGE_LIMIT  # looks fine to len()
    assert _utf16_len(text) > MESSAGE_LIMIT  # but is not

    for part in output.split(text):
        assert _utf16_len(part) <= MESSAGE_LIMIT


def test_splitting_prefers_a_natural_boundary():
    output = make_output(Recorder())
    body = "first paragraph\n\n" + "y" * MESSAGE_LIMIT
    parts = output.split(body)
    assert parts[0].endswith("first paragraph")


def test_split_never_cuts_an_html_tag_in_half():
    output = make_output(Recorder())
    item = make_item(title="T", content="<b>" + "z" * (MESSAGE_LIMIT * 2))
    parts = output.split(output.render(item))
    for part in parts:
        assert part.count("<b>") == part.count("</b>")


# --------------------------------------------------------------------------
# I. HTML escaping
# --------------------------------------------------------------------------
def test_escape_covers_exactly_the_three_significant_characters():
    assert escape("<a> & <b>") == "&lt;a&gt; &amp; &lt;b&gt;"
    # Quotes are not markup-significant in element text, so they stay readable.
    assert escape('say "hi"') == 'say "hi"'


def test_source_text_cannot_inject_markup():
    output = make_output(Recorder())
    item = make_item(title="<script>", content="5 < 6 & 7 > 6")
    message = output.render(item)
    assert "<script>" not in message
    assert "&lt;script&gt;" in message
    assert "5 &lt; 6 &amp; 7 &gt; 6" in message


def test_rendered_metadata_is_escaped_too():
    output = make_output(Recorder())
    item = make_item(publisher="A & B", published_at=None, published_at_raw="<raw>")
    message = output.render(item)
    assert "A &amp; B" in message
    assert "&lt;raw&gt;" in message


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def test_render_carries_the_fields_the_model_actually_has():
    output = make_output(Recorder())
    item = make_item(media=[MediaItem(url="https://img.test/a.jpg")])
    message = output.render(item)
    assert "<b>A title</b>" in message
    assert "hello world" in message
    assert "jack" in message
    assert "https://x.com/jack/status/1" in message
    assert "https://img.test/a.jpg" in message


def test_render_falls_back_to_the_raw_timestamp_and_says_so():
    """Never invent a time (DR-13): show the source's own text, marked."""
    output = make_output(Recorder())
    item = make_item(published_at=None, published_at_raw="Sep 11, 2024")
    message = output.render(item)
    assert "Sep 11, 2024" in message
    assert "原始值" in message


def test_render_has_no_ai_summary_and_no_heading_injection():
    """Formatting is the renderer's job; it adds no derived prose (section 8)."""
    output = make_output(Recorder())
    message = output.render(make_item(content="original text"))
    assert message.count("original text") == 1


# --------------------------------------------------------------------------
# J. no credentials
# --------------------------------------------------------------------------
def test_missing_credentials_fail_explicitly_without_calling_the_api():
    recorder = Recorder()
    output = make_output(recorder, token="", chat_id="")
    item = make_item()

    result = output.emit([item])

    assert recorder.requests == []  # no HTTP at all
    assert result.delivered == 0
    assert result.failed == 1
    assert item.identity_key in result.failed_keys
    assert "bot token" in result.detail
    assert "chat id" in result.detail


def test_missing_credentials_are_named_but_never_valued():
    output = make_output(Recorder(), token="", chat_id="")
    reason = output.unavailable_reason()
    assert "bot token" in reason
    assert CHAT_ID not in reason  # the configured value must not appear


def test_a_configured_adapter_reports_no_reason():
    output = make_output(Recorder())
    assert output.configured is True
    assert output.unavailable_reason() == ""


def test_credentials_are_read_from_the_environment_by_name_only():
    env = {"MY_TOKEN": " t ", "MY_CHAT": "-100"}
    assert resolve_credentials("MY_TOKEN", "MY_CHAT", env=env) == ("t", "-100")
    # Unset must not raise: the failure has to surface as a delivery failure.
    assert resolve_credentials("NOPE", "ALSO_NOPE", env=env) == ("", "")


# --------------------------------------------------------------------------
# A. success
# --------------------------------------------------------------------------
def test_successful_delivery_records_the_item_as_delivered():
    recorder = Recorder(_ok())
    output = make_output(recorder)
    item = make_item()

    result = output.emit([item])

    assert result.ok is True
    assert result.delivered_keys == [item.identity_key]
    assert result.failed_keys == []
    assert result.attempted == 1
    assert len(recorder.requests) == 1


def test_the_request_is_a_sendmessage_post_to_the_bot_endpoint():
    recorder = Recorder(_ok())
    output = make_output(recorder)
    output.emit([make_item()])

    request = recorder.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == f"{API}/bot{TOKEN}/sendMessage"
    assert request["body"]["chat_id"] == CHAT_ID
    assert request["body"]["parse_mode"] == "HTML"


def test_an_empty_batch_does_nothing():
    recorder = Recorder()
    result = make_output(recorder).emit([])
    assert recorder.requests == []
    assert result.attempted == 0
    assert result.ok is True


# --------------------------------------------------------------------------
# B. HTTP error statuses
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status, description",
    [
        (400, "Bad Request: chat not found"),
        (401, "Unauthorized"),
        (403, "Forbidden: bot was blocked by the user"),
        (500, "Internal Server Error"),
    ],
)
def test_http_error_statuses_fail_the_item_and_keep_it_pending(status, description):
    recorder = Recorder(_error(status, description))
    output = make_output(recorder)
    item = make_item()

    result = output.emit([item])

    assert result.delivered == 0
    assert result.failed_keys == [item.identity_key]
    assert str(status) in result.detail
    assert description in result.detail


def test_a_200_with_ok_false_is_a_failure():
    """``ok`` is authoritative: 200 + ok:false means nothing was delivered."""
    recorder = Recorder(httpx.Response(200, json={"ok": False, "description": "nope"}))
    item = make_item()

    result = make_output(recorder).emit([item])

    assert result.delivered == 0
    assert item.identity_key in result.failed_keys
    assert "nope" in result.detail


def test_a_429_is_retried_and_then_succeeds():
    recorder = Recorder(_error(429, "Too Many Requests", retry_after=2), _ok())
    output = make_output(recorder)

    result = output.emit([make_item()])

    assert result.ok is True
    assert len(recorder.requests) == 2
    assert output.slept == [2.0]  # type: ignore[attr-defined]


def test_a_persistent_429_gives_up_without_hanging():
    recorder = Recorder(*[_error(429, "Too Many Requests", retry_after=1)] * 3)
    output = make_output(recorder)

    result = output.emit([make_item()])

    assert result.failed == 1
    assert len(recorder.requests) == 3  # initial + MAX_RATE_LIMIT_RETRIES
    assert output.slept == [1.0, 1.0]  # type: ignore[attr-defined]


def test_a_retry_after_longer_than_the_cap_is_clamped():
    recorder = Recorder(_error(429, "Too Many Requests", retry_after=3600), _ok())
    output = make_output(recorder)

    output.emit([make_item()])

    assert output.slept == [30.0]  # type: ignore[attr-defined]


def test_a_non_json_error_body_is_still_a_failure():
    recorder = Recorder(httpx.Response(502, text="<html>bad gateway</html>"))
    item = make_item()

    result = make_output(recorder).emit([item])

    assert result.failed == 1
    assert "502" in result.detail


# --------------------------------------------------------------------------
# C. network exceptions
# --------------------------------------------------------------------------
def test_a_timeout_is_reported_and_not_raised():
    recorder = Recorder(httpx.TimeoutException("timed out"))
    item = make_item()

    result = make_output(recorder).emit([item])

    assert result.failed_keys == [item.identity_key]
    assert "timeout" in result.detail


def test_a_connection_error_is_reported_and_not_raised():
    recorder = Recorder(httpx.ConnectError("connection refused"))
    item = make_item()

    result = make_output(recorder).emit([item])

    assert result.failed_keys == [item.identity_key]
    assert "connection" in result.detail


def test_an_unexpected_exception_does_not_escape_the_adapter():
    recorder = Recorder(RuntimeError("kaboom"))
    item = make_item()

    result = make_output(recorder).emit([item])

    assert result.failed_keys == [item.identity_key]
    assert "kaboom" in result.detail


# --------------------------------------------------------------------------
# secret hygiene (task section 20)
# --------------------------------------------------------------------------
def test_the_token_never_appears_in_a_reported_detail():
    """The token is in the request URL, so an exception can carry it out.

    Every error path is redacted for exactly this reason.
    """
    recorder = Recorder(httpx.ConnectError(f"failed to connect to /bot{TOKEN}/sendMessage"))
    item = make_item()

    result = make_output(recorder).emit([item])

    assert TOKEN not in result.detail
    assert "***" in result.detail


def test_the_token_never_appears_in_a_rendered_message():
    output = make_output(Recorder())
    message = output.render(make_item())
    assert TOKEN not in message


# --------------------------------------------------------------------------
# D. per-item outcomes / partial delivery
# --------------------------------------------------------------------------
def test_a_repeated_failure_reason_is_reported_once():
    """A batch failure has one cause; the detail must not repeat it per item.

    Found by the Phase 3 smoke run: a dead port made all 31 items fail with the
    same "timed out" clause, and the concatenated detail was that sentence 31
    times over — which buries the actual reason in noise.
    """
    recorder = Recorder(*[_error(500, "Internal Server Error")] * 3)
    output = make_output(recorder)
    items = [make_item(str(n)) for n in range(3)]

    result = output.emit(items)

    assert result.failed == 3
    assert result.detail.count("HTTP 500") == 1
    assert "Internal Server Error" in result.detail


def test_distinct_failure_reasons_are_reported_but_capped():
    from outputs.base import MAX_DETAIL_PARTS

    # Kept below MAX_CONSECUTIVE_FAILURES so this measures capping, not the
    # batch abort (which has its own test).
    count = MAX_DETAIL_PARTS + 1
    recorder = Recorder(*[_error(400 + n, f"reason {n}") for n in range(count)])
    output = make_output(recorder)
    items = [make_item(str(n)) for n in range(count)]

    result = output.emit(items)

    assert "reason 0" in result.detail
    assert f"reason {MAX_DETAIL_PARTS - 1}" in result.detail
    assert f"reason {MAX_DETAIL_PARTS}" not in result.detail
    assert "+1 more distinct reason" in result.detail
    assert result.failed == count


def test_a_run_of_failures_aborts_the_batch_and_keeps_everything_pending():
    """A systemic problem must not cost one timeout per pending item.

    Sending is sequential, so an unreachable host costs the full timeout for
    every item. The smoke run showed 31 items taking 62 seconds; a real backlog
    would exceed the workflow's 20-minute budget and get the job killed
    mid-run. Aborting keeps every unsent item pending, so nothing is lost.
    """
    from outputs.telegram import MAX_CONSECUTIVE_FAILURES

    total = MAX_CONSECUTIVE_FAILURES + 4
    recorder = Recorder(*[httpx.TimeoutException("timed out")] * total)
    output = make_output(recorder)
    items = [make_item(str(n)) for n in range(total)]

    result = output.emit(items)

    # Exactly the threshold was attempted; the rest were never sent.
    assert len(recorder.requests) == MAX_CONSECUTIVE_FAILURES
    assert result.failed == total  # every item accounted for
    assert result.delivered == 0
    assert "aborted after" in result.detail
    assert f"{total - MAX_CONSECUTIVE_FAILURES} item(s) left pending" in result.detail


def test_the_abort_counter_resets_after_a_success():
    """One bad item must not poison the rest of the batch."""
    from outputs.telegram import MAX_CONSECUTIVE_FAILURES

    replies = []
    for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
        replies.append(_error(500, "Internal Server Error"))
    replies.append(_ok())  # resets the counter
    replies.append(_error(500, "Internal Server Error"))
    recorder = Recorder(*replies)
    output = make_output(recorder)
    items = [make_item(str(n)) for n in range(len(replies))]

    result = output.emit(items)

    assert len(recorder.requests) == len(replies)  # never aborted
    assert result.delivered == 1
    assert result.failed == len(replies) - 1


def test_one_failure_does_not_lose_the_other_items():
    recorder = Recorder(_ok(), _error(400, "Bad Request: chat not found"), _ok())
    output = make_output(recorder)
    items = [make_item("1"), make_item("2"), make_item("3")]

    result = output.emit(items)

    assert result.delivered == 2
    assert result.failed == 1
    assert result.failed_keys == [items[1].identity_key]
    assert len(recorder.requests) == 3


def test_a_partially_sent_item_is_not_delivered():
    """Part 1 accepted, part 2 rejected ⇒ Delivered=false (task section 10).

    The whole item stays pending so the next run retries it. Half a digest is
    not a delivery, and marking it done would lose the remainder silently.
    """
    big = make_item(content="q" * (MESSAGE_LIMIT * 2))
    recorder = Recorder(_ok(), _error(400, "Bad Request: message is too long"))
    output = make_output(recorder)

    result = output.emit([big])

    assert len(recorder.requests) == 2
    assert result.delivered == 0
    assert result.failed_keys == [big.identity_key]
    assert "part 1/" in result.detail or "part 2/" in result.detail


def test_an_item_that_renders_empty_is_skipped_not_delivered():
    """Defensive: nothing to send must be *visible*, not silently marked sent.

    A contract-valid item always renders something, so this is unreachable
    through the normal path — but if it ever happens, the item must stay
    pending rather than be recorded as delivered.
    """
    recorder = Recorder()
    output = make_output(recorder)
    item = make_item(
        title=None,
        content="   ",
        publisher=None,
        canonical_url="",
        published_at=None,
        published_at_raw="",
    )

    result = output.emit([item])

    assert recorder.requests == []
    assert result.delivered == 0
    assert result.failed == 0
    assert result.skipped_keys == [item.identity_key]


# --------------------------------------------------------------------------
# F. output namespace isolation
# --------------------------------------------------------------------------
def test_the_adapter_declares_its_own_stable_name():
    assert make_output(Recorder()).name == "telegram"


def test_two_adapters_do_not_share_a_name():
    """Delivery state is namespaced by name, so a collision would corrupt it."""
    from outputs.base import OutputAdapter

    class FakeQQ(OutputAdapter):
        name = "qq"

        def emit(self, items):
            raise NotImplementedError

    assert TelegramOutput.name != FakeQQ.name


# --------------------------------------------------------------------------
# G. duplicate execution
# --------------------------------------------------------------------------
def test_the_adapter_itself_does_not_deduplicate():
    """Deduplication is storage's job via pending_for(), not the adapter's.

    An adapter that kept its own memory of what it sent would be a second
    source of truth — exactly what the freeze forbids. So sending the same item
    twice must send twice; the caller is responsible for only passing pending
    items.
    """
    recorder = Recorder(_ok(), _ok())
    output = make_output(recorder)
    item = make_item()

    output.emit([item])
    output.emit([item])

    assert len(recorder.requests) == 2


# --------------------------------------------------------------------------
# E. retry after failure
# --------------------------------------------------------------------------
def test_a_previously_failed_item_can_be_retried_successfully():
    item = make_item()
    failing = make_output(Recorder(_error(500, "Internal Server Error")))
    first = failing.emit([item])
    assert first.failed_keys == [item.identity_key]

    working = make_output(Recorder(_ok()))
    second = working.emit([item])
    assert second.delivered_keys == [item.identity_key]


def test_close_releases_only_an_owned_client():
    """An injected client belongs to the caller and must not be closed."""
    injected = httpx.Client(transport=httpx.MockTransport(Recorder()))
    output = TelegramOutput(bot_token=TOKEN, chat_id=CHAT_ID, client=injected)
    output.close()
    assert injected.is_closed is False
    injected.close()


def test_close_is_safe_to_call_twice():
    output = make_output(Recorder())
    output.close()
    output.close()
