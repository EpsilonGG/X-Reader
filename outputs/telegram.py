"""Telegram output adapter — the first real ``OutputAdapter``.

What this proves (task section 27): Architecture Freeze v3's output/delivery
design can absorb a real external platform **without** touching the shared data
model, the providers, the parsers or storage. Nothing in this module imports a
provider, a parser, a normalizer or storage; it receives ``NormalizedItem``
objects and reports per-item outcomes.

The contract it obeys
---------------------
* it does **not** record its own delivery state — it returns a
  :class:`~outputs.base.DeliveryResult` and the caller persists it through
  Storage, which stays the single source of truth;
* it does **not** write to ``data/``, does not decide identity, does not
  deduplicate, does not fetch;
* it never mutates the items it is given (they are other adapters' input too).

Transport
---------
The Telegram Bot API over plain ``httpx`` — no SDK. ``httpx`` is already a
runtime dependency, and the whole surface used here is one POST, so a
notification SDK would be a large dependency for a small need (task section 6).

Credentials
-----------
The bot token and chat id are **read from the environment**, never from
``config.yaml``, never from code, never written to ``data/`` or to a log line.
Every message this module produces is passed through :func:`_redact`, because
the token is part of the request URL and would otherwise leak through an
exception string — the one place a secret escapes without anyone noticing.

Long items
----------
Telegram rejects messages over 4096 characters. An item is split into parts and
the item counts as delivered **only if every part was accepted**; a partially
sent item stays pending, so the next run retries it. Splitting happens on the
raw text *before* escaping, so a split can never cut an HTML tag in half.

Failing fast
------------
Sending is sequential, so a systemic failure — dead network, wrong chat id,
revoked token — costs one timeout *per pending item*. The Phase 3 smoke run made
this concrete: 31 items against an unreachable host took 62 seconds, which for a
real backlog would exceed the CI job's budget and get the run killed mid-write.
After :data:`MAX_CONSECUTIVE_FAILURES` failures in a row the batch stops and
everything unsent stays pending — the same outcome as failing each one, without
spending the budget to discover it. A single success resets the counter, so one
bad item cannot poison the batch.
"""
from __future__ import annotations

import html
import logging
import os
import time
from typing import Callable

import httpx

from domain.models.item import NormalizedItem
from outputs.base import DeliveryResult
from outputs.base import MAX_CONSECUTIVE_FAILURES
from outputs.base import OutputAdapter
from outputs.base import record_batch_failure
from outputs.base import redact

logger = logging.getLogger("x_reader.outputs.telegram")

#: The adapter's stable id — also its delivery-state namespace in storage.
OUTPUT_NAME = "telegram"

DEFAULT_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 20

#: Telegram's hard limit, in UTF-16 code units (see :func:`_utf16_len`).
MESSAGE_LIMIT = 4096

#: Telegram HTML is the least fragile rich mode: it needs only three escapes and
#: has no nested/inline ambiguity to get wrong.
PARSE_MODE = "HTML"

#: A digest is mostly links; one preview card per message would dwarf the text.
DISABLE_WEB_PAGE_PREVIEW = True

#: A 429 is the one failure worth retrying immediately, because Telegram tells us
#: exactly how long to wait. Everything else is reported and left pending.
MAX_RATE_LIMIT_RETRIES = 2
MAX_RETRY_AFTER_SECONDS = 30.0

#: The batch-abort threshold is protocol-independent, so it lives in
#: ``outputs/base.py`` alongside the other shared output concerns and is
#: re-exported here because Telegram is where it was first needed. See that
#: constant for why a sequential sender has to give up early.
__all__ = [
    "OUTPUT_NAME",
    "MAX_CONSECUTIVE_FAILURES",
    "MAX_RATE_LIMIT_RETRIES",
    "MESSAGE_LIMIT",
    "TelegramOutput",
    "escape",
    "resolve_credentials",
]


def _utf16_len(text: str) -> int:
    """Length in UTF-16 code units, which is what Telegram actually counts.

    ``len()`` counts code points, so an emoji or a rare CJK ideograph counts as
    one here but two there. Splitting on ``len()`` would therefore let a message
    that "looks" under the limit be rejected. This project's content is largely
    Japanese, where that gap is real.

    Telegram-specific, so it stays here: OneBot has no equivalent limit, and
    moving this into ``outputs/base.py`` would imply every adapter needs it.
    """
    return len(text.encode("utf-16-le")) // 2


def escape(text: str) -> str:
    """Escape text for Telegram's HTML parse mode.

    Only ``&``, ``<`` and ``>`` are markup-significant inside element text, so
    quotes are left alone — escaping them would make ordinary prose noisier for
    no gain. Callers must escape *every* piece of source-derived text; feeding
    raw tweet or feed text into a message is how a stray ``<`` silently breaks
    delivery for the whole batch.
    """
    return html.escape(text, quote=False)


def _chunk(text: str, budget: int, measure: Callable[[str], int]) -> list[str]:
    """Split ``text`` into the fewest chunks that each ``measure`` at most ``budget``.

    ``measure`` is monotonic in prefix length, which is what makes the binary
    search for the largest fitting prefix valid.

    Breaks on a paragraph, then a line, then a space, and only then mid-word —
    so a split stays readable whenever the text offers a natural boundary.
    """
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while remaining:
        if measure(remaining) <= budget:
            chunks.append(remaining)
            break

        # Find the largest prefix that fits, then back off to a natural break.
        low, high = 1, len(remaining)
        while low < high:
            mid = (low + high + 1) // 2
            if measure(remaining[:mid]) <= budget:
                low = mid
            else:
                high = mid - 1
        cut = low

        window = remaining[:cut]
        for separator in ("\n\n", "\n", " "):
            index = window.rfind(separator)
            if index > cut // 2:
                cut = index + len(separator)
                break

        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def _chunk_raw(text: str, budget: int) -> list[str]:
    """Split **unescaped** text; ``budget`` applies to the escaped result.

    Splitting the raw string and escaping afterwards (rather than the reverse)
    is what makes this safe: an escaped chunk is always a complete, well-formed
    piece of text, so no entity or tag can be cut in half.
    """
    return _chunk(text, budget, measure=lambda value: _utf16_len(escape(value)))


def _chunk_escaped(text: str, budget: int) -> list[str]:
    """Split **already-escaped** text, measuring it directly.

    A separate entry point because re-measuring escaped text through
    :func:`escape` would escape it a second time — ``&amp;`` would become
    ``&amp;amp;`` and every ``<b>`` would be sent literally.
    """
    return _chunk(text, budget, measure=_utf16_len)


class TelegramOutput(OutputAdapter):
    """Send normalized items to a Telegram chat through a bot.

    Constructed even when credentials are missing: a deployment that turns
    Telegram on without configuring the secrets must fail *loudly at delivery
    time and keep the items pending*, not crash the run or silently drop
    content (task section 17).
    """

    name = OUTPUT_NAME

    def __init__(
        self,
        *,
        bot_token: str = "",
        chat_id: str = "",
        api_base: str = DEFAULT_API_BASE,
        timeout: int = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        max_length: int = MESSAGE_LIMIT,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.bot_token = (bot_token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.max_length = max_length
        # Kept as a plain number rather than read back off the client: httpx
        # exposes ``Client.timeout`` as a ``Timeout`` object, which is not
        # formattable and exists to configure retries, not to report them.
        self.timeout = timeout
        self._sleep = sleep
        self._owned_client = client is None
        # Injected transport is the test seam: status-code mapping, retry and
        # splitting are the parts worth testing, and none can be exercised
        # honestly against the live Bot API.
        self._client = client or httpx.Client(timeout=timeout, transport=transport)

    # -- capability ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        """True when both credentials are present."""
        return bool(self.bot_token and self.chat_id)

    def unavailable_reason(self) -> str:
        """Why this adapter cannot send. Never contains a credential value."""
        missing = []
        if not self.bot_token:
            missing.append("bot token")
        if not self.chat_id:
            missing.append("chat id")
        if not missing:
            return ""
        return (
            f"telegram is enabled but {' and '.join(missing)} is missing; "
            "set it in the environment (locally) or in GitHub repository secrets"
        )

    # -- rendering ----------------------------------------------------------
    def render(self, item: NormalizedItem) -> str:
        """One item as one Telegram HTML message.

        Deliberately plain: title, body, origin, time, link, one attachment.
        Formatting is a renderer concern and this is the renderer — none of it
        touches ``NormalizedItem`` (task sections 8 / 9).
        """
        blocks: list[str] = []

        if item.title:
            for chunk in _chunk_raw(item.title, self.max_length - len("<b></b>")):
                blocks.append(f"<b>{escape(chunk)}</b>")

        if item.content:
            blocks.extend(escape(chunk) for chunk in _chunk_raw(item.content, self.max_length))

        meta: list[str] = []
        if item.publisher:
            meta.append(f"来源：{escape(item.publisher)}")
        # ``published_at`` is the source's claim; when it is absent the source's
        # own rendering is shown verbatim rather than inventing a time (DR-13).
        if item.published_at:
            meta.append(f"时间：{escape(item.published_at)}")
        elif item.published_at_raw:
            meta.append(f"时间：{escape(item.published_at_raw)}（原始值）")
        if meta:
            blocks.append("\n".join(meta))

        if item.canonical_url:
            blocks.append(escape(item.canonical_url))

        # One representative attachment. A full gallery would dominate the
        # message; the link carries the rest.
        if item.media and item.media[0].url:
            blocks.append(f"附件：{escape(item.media[0].url)}")

        return "\n\n".join(block for block in blocks if block)

    def split(self, message: str) -> list[str]:
        """Split an already-rendered message into sendable parts.

        Every part is guaranteed to be within the limit; a message that already
        fits comes back as a single part.
        """
        if _utf16_len(message) <= self.max_length:
            return [message]

        parts: list[str] = []
        current = ""
        for block in message.split("\n\n"):
            candidate = f"{current}\n\n{block}" if current else block
            if _utf16_len(candidate) <= self.max_length:
                current = candidate
                continue
            if current:
                parts.append(current)
                current = ""
            if _utf16_len(block) <= self.max_length:
                current = block
                continue
            # A single block that is itself too long (only possible for content
            # that arrived without any natural break). Already escaped, so it
            # must be measured as-is rather than escaped again.
            for chunk in _chunk_escaped(block, self.max_length):
                parts.append(chunk)
        if current:
            parts.append(current)
        return parts

    # -- delivery -----------------------------------------------------------
    def emit(self, items: list[NormalizedItem]) -> DeliveryResult:
        """Deliver ``items``. Never raises: one bad item must not lose the rest."""
        result = DeliveryResult(output=self.name)
        if not items:
            return result

        reason = self.unavailable_reason()
        if reason:
            # Explicit, item-preserving failure: nothing is marked delivered, so
            # the next run retries once the secret is configured.
            logger.error("%s: %d item(s) left pending", reason, len(items))
            record_batch_failure(result, items, reason)
            return result

        consecutive_failures = 0
        for position, item in enumerate(items):
            message = self.render(item)
            if not message.strip():
                # Nothing sendable. Skipped, not delivered — an item an adapter
                # cannot send must stay visible rather than be marked done.
                result.skip(item.identity_key, detail="rendered empty")
                logger.warning("%s: %s rendered empty, left pending", self.name, item.identity_key)
                continue

            parts = self.split(message)
            failed = ""
            for index, part in enumerate(parts, start=1):
                ok, detail = self._send_part(part, item.identity_key, index, len(parts))
                if not ok:
                    failed = detail
                    break

            if failed:
                result.record(item.identity_key, ok=False, detail=failed)
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._abort_batch(result, items[position + 1:], consecutive_failures)
                    return result
            else:
                result.record(item.identity_key, ok=True)
                consecutive_failures = 0

        return result

    def _abort_batch(
        self, result: DeliveryResult, remaining: list[NormalizedItem], failures: int
    ) -> None:
        """Give up on the rest of the batch, leaving every item pending.

        Reached only after a run of consecutive failures, which means the problem
        is the connection, not the content. Nothing is marked delivered, so the
        next run picks all of it up.
        """
        reason = (
            f"aborted after {failures} consecutive failures; "
            f"{len(remaining)} item(s) left pending for the next run"
        )
        logger.error("%s: %s", self.name, reason)
        record_batch_failure(result, remaining, reason)

    def _send_part(
        self, text: str, identity_key: str, index: int, total: int
    ) -> tuple[bool, str]:
        """Send one part. Returns ``(ok, redacted_detail)``; never raises."""
        url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": PARSE_MODE,
            "disable_web_page_preview": DISABLE_WEB_PAGE_PREVIEW,
        }

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                response = self._client.post(url, json=payload)
            except httpx.TimeoutException as exc:
                return False, self._safe(f"timeout after {self.timeout}s: {exc}")
            except httpx.TransportError as exc:
                return False, self._safe(f"connection error: {exc}")
            except Exception as exc:  # noqa: BLE001 - a client bug must not kill the run
                return False, self._safe(f"unexpected error: {exc!r}")

            status = response.status_code
            description = self._description(response)

            if status == 200 and description == "":
                if total > 1:
                    logger.info(
                        "%s: %s part %d/%d sent", self.name, identity_key, index, total
                    )
                return True, ""

            if status == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                delay = min(self._retry_after(response), MAX_RETRY_AFTER_SECONDS)
                logger.warning(
                    "%s: rate limited (429), retrying in %.0fs", self.name, delay
                )
                self._sleep(delay)
                continue

            detail = f"HTTP {status}" + (f": {description}" if description else "")
            if total > 1:
                detail = f"part {index}/{total} {detail}"
            return False, detail

        return False, "rate limited: retries exhausted"

    # -- helpers ------------------------------------------------------------
    def _safe(self, text: str) -> str:
        """Redact anything that could carry the token out of a message."""
        return redact(text, self.bot_token)

    @staticmethod
    def _description(response: httpx.Response) -> str:
        """Telegram's own error text, or ``""`` when the call succeeded.

        ``ok`` is authoritative: Telegram has been known to answer 200 with
        ``ok: false``, and treating that as success would mark an undelivered
        item as delivered.
        """
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON body is just "no detail"
            return "" if response.status_code == 200 else "no response body"
        if not isinstance(body, dict):
            return "" if response.status_code == 200 else "malformed response"
        if body.get("ok") is True:
            return ""
        return str(body.get("description") or "ok=false")

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            body = response.json()
            value = (body.get("parameters") or {}).get("retry_after")
            if value is not None:
                return max(0.0, float(value))
        except Exception:  # noqa: BLE001
            pass
        return 1.0

    def close(self) -> None:
        """Close the client this adapter owns. Never raises."""
        if self._owned_client:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


def resolve_credentials(
    bot_token_env: str, chat_id_env: str, env: dict | None = None
) -> tuple[str, str]:
    """Read the two secrets from the environment by their configured names.

    Only the *names* live in ``config.yaml``; the values never do. A blank or
    unset variable returns ``""`` rather than raising, so the failure surfaces
    as a delivery failure that keeps items pending instead of a start-up crash
    that would also stop X/RSS acquisition.
    """
    source = os.environ if env is None else env
    return (
        (source.get(bot_token_env) or "").strip(),
        (source.get(chat_id_env) or "").strip(),
    )
