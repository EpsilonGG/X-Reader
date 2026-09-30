"""QQ / OneBot output adapter tests.

The adapter is unit-tested against an ``httpx.MockTransport`` — the only honest
way to exercise status/retcode mapping, request construction and redaction, none
of which can be provoked against a real OneBot on demand — and then against a
**real socket** in one end-to-end case, so "it works over HTTP" is not merely
asserted against a mock.

No OneBot server is available in this workspace and none is faked: the mock
server below speaks real HTTP on a real port, and the adapter reaches it through
a real ``httpx.Client``. A test that pretended to have sent a message would be
worse than no test at all.

What is *not* here: anything about where items came from. This file never
mentions a provider, a parser or a normalizer, because the adapter never does.
Delivery persistence is checked against real storage in ``tests/test_multi_output.py``.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

import httpx
import pytest

from config.schema import Config
from domain.models.item import PRECISION_SECOND
from domain.models.item import SOURCE_X
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from outputs.base import MAX_CONSECUTIVE_FAILURES
from outputs.factory import build_outputs
from outputs.factory import output_names
from outputs.qq import ACTION_SEND_GROUP_MSG
from outputs.qq import MAX_CONTENT_CHARS
from outputs.qq import MAX_TITLE_CHARS
from outputs.qq import QQOutput
from outputs.qq import TRUNCATION_MARKER
from outputs.qq import resolve_access_token

TOKEN = "fake-onebot-token"
GROUP_ID = "123456789"
API = "http://onebot.test:3000"

MINIMAL_X_CONFIG = {
    "provider": {"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}}
}


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


def _ok(retcode: int = 0, **extra) -> httpx.Response:
    body = {"status": "ok" if retcode == 0 else "failed", "retcode": retcode, "data": {"message_id": 7}}
    body.update(extra)
    return httpx.Response(200, json=body)


def _failed(retcode: int, msg: str = "send failed") -> httpx.Response:
    return httpx.Response(200, json={"status": "failed", "retcode": retcode, "msg": msg})


class Recorder:
    """A scripted OneBot endpoint: records every request, replies from a queue."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append(
            {
                "url": str(request.url),
                "method": request.method,
                "body": body,
                "headers": dict(request.headers),
            }
        )
        reply = self.replies.pop(0) if self.replies else _ok()
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def texts(self) -> list[str]:
        """The text of every text segment sent, in order."""
        texts = []
        for request in self.requests:
            for segment in request["body"].get("message") or []:
                if segment.get("type") == "text":
                    texts.append(segment.get("data", {}).get("text", ""))
        return texts

    @property
    def groups(self) -> list:
        return [request["body"].get("group_id") for request in self.requests]


def make_output(recorder: Recorder, **kwargs) -> QQOutput:
    """An adapter wired to a scripted transport."""
    options = {
        "api_base": API,
        "group_id": GROUP_ID,
        "access_token": TOKEN,
    }
    options.update(kwargs)
    return QQOutput(transport=httpx.MockTransport(recorder), **options)


# --------------------------------------------------------------------------
# configuration and factory
# --------------------------------------------------------------------------
def test_qq_is_absent_when_disabled():
    config = Config(**MINIMAL_X_CONFIG)
    assert build_outputs(config, env={}) == []
    assert output_names(config) == []


def test_the_factory_builds_a_qq_adapter_when_enabled():
    config = Config(
        **MINIMAL_X_CONFIG,
        qq={"enabled": True, "api_base": API, "group_id": GROUP_ID},
    )
    outputs = build_outputs(config, env={})
    assert [o.name for o in outputs] == ["qq"]
    assert isinstance(outputs[0], QQOutput)
    assert output_names(config) == ["qq"]


def test_an_enabled_qq_without_a_group_id_is_rejected_at_load_time():
    """QQ's target is configuration, not a secret, so it fails *earlier* than Telegram.

    Telegram's credentials live in the environment, so a valid config can still
    yield an unconfigured adapter — which is why that adapter must fail at
    delivery time. QQ's ``api_base`` and ``group_id`` are in the config file, so
    an incomplete QQ is caught by validation instead, before any network traffic.
    The delivery-time guard still exists for a directly constructed adapter; see
    ``test_an_unconfigured_adapter_fails_every_item_and_sends_nothing``.
    """
    with pytest.raises(Exception) as excinfo:
        Config(**MINIMAL_X_CONFIG, qq={"enabled": True, "api_base": API})
    assert "group_id" in str(excinfo.value)


def test_the_factory_reads_the_token_from_the_environment_by_name():
    config = Config(
        **MINIMAL_X_CONFIG,
        qq={
            "enabled": True,
            "api_base": API,
            "group_id": GROUP_ID,
            "access_token_env": "MY_ONEBOT_TOKEN",
        },
    )
    outputs = build_outputs(config, env={"MY_ONEBOT_TOKEN": "s3cret"})
    assert outputs[0].access_token == "s3cret"


def test_the_factory_sends_no_token_when_the_variable_is_unset():
    """A OneBot with no access token is a normal setup, not a misconfiguration."""
    config = Config(
        **MINIMAL_X_CONFIG,
        qq={"enabled": True, "api_base": API, "group_id": GROUP_ID},
    )
    outputs = build_outputs(config, env={})
    assert outputs[0].access_token == ""
    assert outputs[0].configured is True


def test_resolve_access_token_returns_empty_for_a_blank_name():
    assert resolve_access_token("", env={"X": "y"}) == ""
    assert resolve_access_token("MISSING", env={}) == ""


def test_both_outputs_can_be_enabled_together():
    config = Config(
        **MINIMAL_X_CONFIG,
        telegram={"enabled": True},
        qq={"enabled": True, "api_base": API, "group_id": GROUP_ID},
    )
    env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1"}
    assert output_names(config) == ["telegram", "qq"]
    assert [o.name for o in build_outputs(config, env=env)] == ["telegram", "qq"]


def test_no_credential_is_stored_on_the_config_object():
    config = Config(
        **MINIMAL_X_CONFIG,
        qq={"enabled": True, "api_base": API, "group_id": GROUP_ID},
    )
    assert not hasattr(config.qq, "access_token")


# --------------------------------------------------------------------------
# request construction
# --------------------------------------------------------------------------
def test_the_request_targets_the_send_group_msg_action():
    recorder = Recorder()
    make_output(recorder).emit([make_item()])
    assert recorder.requests[0]["url"] == f"{API}{ACTION_SEND_GROUP_MSG}"
    assert recorder.requests[0]["method"] == "POST"


def test_a_numeric_group_id_is_sent_as_a_number():
    """OneBot types ``group_id`` as int64; strict implementations reject a string."""
    recorder = Recorder()
    make_output(recorder, group_id="123456789").emit([make_item()])
    assert recorder.groups == [123456789]
    assert isinstance(recorder.groups[0], int)


def test_a_non_numeric_group_id_is_passed_through_as_a_string():
    recorder = Recorder()
    make_output(recorder, group_id="group-abc").emit([make_item()])
    assert recorder.groups == ["group-abc"]


def test_the_message_is_one_text_segment_not_a_cq_string():
    """A segment cannot be reinterpreted, so content is literal by construction."""
    recorder = Recorder()
    make_output(recorder).emit([make_item(title="[CQ:at,qq=all] hi")])
    message = recorder.requests[0]["body"]["message"]
    assert isinstance(message, list)
    assert len(message) == 1
    assert message[0]["type"] == "text"
    assert message[0]["data"]["text"].startswith("【[CQ:at,qq=all] hi】")


def test_the_token_is_sent_as_a_bearer_header():
    recorder = Recorder()
    make_output(recorder, access_token=TOKEN).emit([make_item()])
    assert recorder.requests[0]["headers"]["authorization"] == f"Bearer {TOKEN}"


def test_no_authorization_header_is_sent_without_a_token():
    recorder = Recorder()
    make_output(recorder, access_token="").emit([make_item()])
    assert "authorization" not in recorder.requests[0]["headers"]


def test_a_trailing_slash_on_the_api_base_is_tolerated():
    recorder = Recorder()
    make_output(recorder, api_base=f"{API}/").emit([make_item()])
    assert recorder.requests[0]["url"] == f"{API}{ACTION_SEND_GROUP_MSG}"


def test_one_request_per_item():
    recorder = Recorder()
    make_output(recorder).emit([make_item("1"), make_item("2"), make_item("3")])
    assert len(recorder.requests) == 3


# --------------------------------------------------------------------------
# message content
# --------------------------------------------------------------------------
def test_the_message_carries_title_source_time_and_link():
    message = make_output(Recorder()).render(make_item())
    assert "【A title】" in message
    assert "hello world" in message
    assert "来源：jack" in message
    assert "时间：2024-09-11T19:31:00+00:00" in message
    assert "链接：https://x.com/jack/status/1" in message


def test_the_raw_time_is_shown_when_there_is_no_parsed_time():
    message = make_output(Recorder()).render(
        make_item(published_at=None, published_at_raw="Sep 11, 2024")
    )
    assert "时间：Sep 11, 2024（原始值）" in message
    assert "2024-09-11T19:31" not in message


def test_a_missing_link_does_not_leave_a_dangling_label():
    message = make_output(Recorder()).render(make_item(canonical_url=""))
    assert "链接：" not in message


def test_the_title_is_bounded():
    message = make_output(Recorder()).render(make_item(title="t" * 500))
    assert TRUNCATION_MARKER in message
    assert len(message) < 500


def test_the_content_is_bounded():
    message = make_output(Recorder()).render(make_item(content="c" * 5000))
    assert TRUNCATION_MARKER in message
    # Title + a bounded excerpt + the meta block, comfortably inside any limit.
    assert len(message) < MAX_TITLE_CHARS + MAX_CONTENT_CHARS + 200


def test_the_link_survives_even_when_the_content_is_truncated():
    """A truncated excerpt costs convenience; a lost link costs access."""
    item = make_item(content="c" * 5000)
    message = make_output(Recorder()).render(item)
    assert item.canonical_url in message


def test_whitespace_is_collapsed_so_a_multiline_body_stays_one_message():
    message = make_output(Recorder()).render(make_item(content="a\n\n\nb\tc   d"))
    assert "a b c d" in message


def test_an_item_with_no_title_and_no_content_renders_empty():
    """Only possible via the meta block; the meta block alone is still sendable."""
    message = make_output(Recorder()).render(make_item(title="", content=""))
    assert "来源：jack" in message


def test_an_item_with_nothing_at_all_renders_empty_and_is_skipped():
    recorder = Recorder()
    result = make_output(recorder).emit(
        [
            make_item(
                title="",
                content="",
                publisher=None,
                canonical_url="",
                published_at=None,
                published_at_raw="",
            )
        ]
    )
    assert result.skipped == 1
    assert result.delivered == 0
    assert recorder.requests == []


def test_media_is_not_sent_in_this_version():
    """Deliberate: an image segment would make delivery depend on OneBot's egress."""
    recorder = Recorder()
    make_output(recorder).emit(
        [make_item(media=[MediaItem(url="https://e.example/a.jpg", type="image")])]
    )
    message = recorder.requests[0]["body"]["message"]
    assert [segment["type"] for segment in message] == ["text"]


# --------------------------------------------------------------------------
# success and failure mapping
# --------------------------------------------------------------------------
def test_retcode_zero_is_a_delivery():
    result = make_output(Recorder(_ok())).emit([make_item()])
    assert result.delivered == 1
    assert result.failed == 0
    assert result.ok is True


def test_a_non_zero_retcode_is_a_failure_with_onebots_message():
    result = make_output(Recorder(_failed(100, "GROUP_NOT_FOUND"))).emit([make_item()])
    assert result.failed == 1
    assert result.delivered == 0
    assert "retcode=100" in result.detail
    assert "GROUP_NOT_FOUND" in result.detail


def test_a_200_with_status_failed_but_retcode_zero_is_a_delivery():
    """``retcode`` is the authoritative field, exactly as ``ok`` is for Telegram."""
    reply = httpx.Response(200, json={"status": "failed", "retcode": 0})
    result = make_output(Recorder(reply)).emit([make_item()])
    assert result.delivered == 1


def test_http_400_is_a_failure():
    result = make_output(Recorder(httpx.Response(400, text="bad request"))).emit([make_item()])
    assert result.failed == 1
    assert "HTTP 400" in result.detail


def test_http_401_is_a_failure():
    result = make_output(Recorder(httpx.Response(401, text="unauthorized"))).emit([make_item()])
    assert result.failed == 1
    assert "HTTP 401" in result.detail


def test_http_403_is_a_failure():
    result = make_output(Recorder(httpx.Response(403, text="forbidden"))).emit([make_item()])
    assert result.failed == 1
    assert "HTTP 403" in result.detail


def test_http_500_is_a_failure():
    result = make_output(Recorder(httpx.Response(500, text="boom"))).emit([make_item()])
    assert result.failed == 1
    assert "HTTP 500" in result.detail


def test_a_200_with_a_non_json_body_is_a_failure_not_a_success():
    """The conservative direction: a false failure re-sends, a false success loses."""
    reply = httpx.Response(200, text="<html>nginx</html>")
    result = make_output(Recorder(reply)).emit([make_item()])
    assert result.failed == 1
    assert result.delivered == 0
    assert "not JSON" in result.detail


def test_a_200_with_a_non_object_body_is_a_failure():
    reply = httpx.Response(200, json=[1, 2, 3])
    result = make_output(Recorder(reply)).emit([make_item()])
    assert result.failed == 1
    assert "malformed response" in result.detail


def test_a_timeout_is_a_failure():
    result = make_output(Recorder(httpx.TimeoutException("slow"))).emit([make_item()])
    assert result.failed == 1
    assert "timeout" in result.detail


def test_a_connection_error_is_a_failure():
    result = make_output(
        Recorder(httpx.ConnectError("refused"))
    ).emit([make_item()])
    assert result.failed == 1
    assert "connection error" in result.detail


def test_an_unexpected_client_error_is_a_failure_not_a_crash():
    result = make_output(Recorder(RuntimeError("client bug"))).emit([make_item()])
    assert result.failed == 1
    assert "unexpected error" in result.detail


def test_an_unreachable_endpoint_reports_failed_never_sent():
    """The headline requirement: unreachable must not be recorded as sent."""
    result = make_output(
        Recorder(httpx.ConnectError("refused"), httpx.ConnectError("refused"))
    ).emit([make_item("1"), make_item("2")])
    assert result.delivered_keys == []
    assert len(result.failed_keys) == 2
    assert result.ok is False


# --------------------------------------------------------------------------
# credential safety
# --------------------------------------------------------------------------
def test_the_token_never_appears_in_a_failure_detail():
    """A token inside an exception string is the leak nobody notices."""
    error = httpx.ConnectError(f"failed to connect to {API}?access_token={TOKEN}")
    result = make_output(Recorder(error), access_token=TOKEN).emit([make_item()])
    assert TOKEN not in result.detail
    assert "***" in result.detail


def test_the_token_is_absent_from_the_result_dict():
    result = make_output(Recorder(_failed(100, TOKEN))).emit([make_item()])
    assert TOKEN not in json.dumps(result.to_dict())


def test_no_token_is_placed_in_the_payload_body():
    recorder = Recorder()
    make_output(recorder, access_token=TOKEN).emit([make_item()])
    body = json.dumps(recorder.requests[0]["body"])
    assert TOKEN not in body


# --------------------------------------------------------------------------
# capability
# --------------------------------------------------------------------------
def test_configured_requires_a_group_id_but_not_a_token():
    assert QQOutput(api_base=API, group_id=GROUP_ID).configured is True
    assert QQOutput(api_base=API, group_id="").configured is False


def test_unavailable_reason_names_the_missing_field_without_a_value():
    reason = QQOutput(api_base=API, group_id="").unavailable_reason()
    assert "group id" in reason
    assert API not in reason


def test_an_unconfigured_adapter_fails_every_item_and_sends_nothing():
    recorder = Recorder()
    result = make_output(recorder, group_id="").emit([make_item("1"), make_item("2")])
    assert len(result.failed_keys) == 2
    assert result.delivered == 0
    assert recorder.requests == []


# --------------------------------------------------------------------------
# batch behaviour
# --------------------------------------------------------------------------
def test_a_failure_does_not_stop_the_next_item():
    recorder = Recorder(_failed(100, "bad"), _ok())
    result = make_output(recorder).emit([make_item("1"), make_item("2")])
    assert result.failed_keys == ["x:1"]
    assert result.delivered_keys == ["x:2"]
    assert len(recorder.requests) == 2


def test_the_batch_aborts_after_repeated_failures():
    recorder = Recorder(*[_failed(100, "down") for _ in range(50)])
    items = [make_item(str(i)) for i in range(MAX_CONSECUTIVE_FAILURES + 4)]
    result = make_output(recorder).emit(items)

    assert len(recorder.requests) == MAX_CONSECUTIVE_FAILURES
    assert result.delivered == 0
    assert result.failed == len(items)
    assert f"{len(items) - MAX_CONSECUTIVE_FAILURES} item(s) left pending" in result.detail


def test_a_success_resets_the_abort_counter():
    recorder = Recorder(
        *([_failed(100, "flaky")] * (MAX_CONSECUTIVE_FAILURES - 1)),
        _ok(),
        *([_failed(100, "flaky")] * (MAX_CONSECUTIVE_FAILURES - 1)),
        _ok(),
    )
    items = [make_item(str(i)) for i in range(2 * MAX_CONSECUTIVE_FAILURES)]
    result = make_output(recorder).emit(items)

    assert len(recorder.requests) == 2 * MAX_CONSECUTIVE_FAILURES
    assert len(result.delivered_keys) == 2


def test_an_empty_batch_does_nothing():
    recorder = Recorder()
    result = make_output(recorder).emit([])
    assert result.attempted == 0
    assert recorder.requests == []


def test_close_is_safe_to_call_twice():
    output = make_output(Recorder())
    output.close()
    output.close()


def test_an_injected_client_is_not_closed():
    """The caller owns what the caller passed in."""
    client = httpx.Client(transport=httpx.MockTransport(Recorder()))
    output = QQOutput(api_base=API, group_id=GROUP_ID, client=client)
    output.close()
    assert client.is_closed is False
    client.close()


def test_the_adapter_never_fetches_parses_or_stores():
    output = make_output(Recorder())
    for banned in ("fetch", "parse", "save", "normalize", "pending_for", "mark_delivered"):
        assert not hasattr(output, banned), banned


# --------------------------------------------------------------------------
# real socket
# --------------------------------------------------------------------------
class _OneBotStub:
    """A real HTTP server that answers like OneBot, on a real port."""

    def __init__(self, *, retcode: int = 0, status: int = 200) -> None:
        self.retcode = retcode
        self.status = status
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8")
                outer.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": json.loads(raw) if raw else {},
                    }
                )
                payload = json.dumps(
                    {"status": "ok", "retcode": outer.retcode, "data": {"message_id": 1}}
                ).encode("utf-8")
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_a_real_socket_delivery_round_trip():
    """Real HTTP, real socket, real JSON — no mock in the path."""
    stub = _OneBotStub()
    try:
        output = QQOutput(
            api_base=stub.base_url,
            group_id=GROUP_ID,
            access_token=TOKEN,
            timeout=10,
        )
        try:
            result = output.emit([make_item("1"), make_item("2")])
        finally:
            output.close()

        assert result.delivered == 2
        assert result.failed == 0
        assert len(stub.requests) == 2
        assert stub.requests[0]["path"] == ACTION_SEND_GROUP_MSG
        assert stub.requests[0]["auth"] == f"Bearer {TOKEN}"
        assert stub.requests[0]["body"]["group_id"] == int(GROUP_ID)
        text = stub.requests[0]["body"]["message"][0]["data"]["text"]
        assert "【A title】" in text
        assert "链接：https://x.com/jack/status/1" in text
    finally:
        stub.stop()


def test_a_real_socket_failure_is_reported_as_failed():
    stub = _OneBotStub(retcode=100)
    try:
        output = QQOutput(api_base=stub.base_url, group_id=GROUP_ID, timeout=10)
        try:
            result = output.emit([make_item()])
        finally:
            output.close()
        assert result.failed == 1
        assert result.delivered == 0
        assert "retcode=100" in result.detail
    finally:
        stub.stop()


def test_a_real_socket_http_error_is_reported_as_failed():
    stub = _OneBotStub(status=500)
    try:
        output = QQOutput(api_base=stub.base_url, group_id=GROUP_ID, timeout=10)
        try:
            result = output.emit([make_item()])
        finally:
            output.close()
        assert result.failed == 1
        assert "HTTP 500" in result.detail
    finally:
        stub.stop()
