"""QQ output adapter — OneBot HTTP API.

The second real ``OutputAdapter``, and the one that proves the output layer
generalises: adding a second delivery target required **no change** to
``NormalizedItem``, Storage, the Runner or the Telegram adapter. The two outputs
share the delivery-state mechanism (``data/delivery/<output>.jsonl``) and nothing
else — each keeps its own formatting, its own failure modes and its own name.

What this is, and what it deliberately is not
---------------------------------------------
* It is a **client of OneBot**: one HTTP POST per message to an OneBot
  implementation the operator already runs.
* It is **not** the official QQ bot API. That would need an app id, a secret, a
  token exchange and an approved bot account.
* It is **not** a QQ SDK, and it does not import one.
* It is **not** a bot server: no long-lived WebSocket, no event loop, no resident
  process. X-Reader runs for a few minutes on a free CI runner and exits, which is
  the only shape the freeze allows.

Credentials, and four things that are easy to confuse
-----------------------------------------------------
===========================  ==========================================
``group_id``                 where the message goes — not a secret
``api_base``                 where OneBot listens — not a secret
``access_token_env``         the *name* of the env var holding the token
the GitHub Secret            the value behind that name
===========================  ==========================================

The access token is read from the environment and sent as a bearer header. It is
never written to ``config.yaml`` (which is committed), never to ``data/``, never
to a log line and never into a report — every message this module produces goes
through :func:`~outputs.base.redact`, because a token inside an exception string
is the one leak nobody notices.

A token is **optional**, unlike Telegram's credentials: a OneBot deployment with
no access token is a normal setup. When no token is configured the header is
simply omitted, rather than the adapter refusing to work.

Why the payload uses a message *segment* rather than a CQ string
----------------------------------------------------------------
OneBot accepts ``message`` either as a string (which it parses for ``[CQ:...]``
codes) or as an array of segments (which it does not). A string payload therefore
turns arbitrary content into a parsing question: a title containing ``[CQ:at,...]``
would be *interpreted* rather than displayed. Sending one ``text`` segment makes
the content literal by construction, so no escaping is needed and no content can
be reinterpreted.

Failure direction
-----------------
``retcode == 0`` is the only signal that counts as success. A 2xx with an
unparseable body is treated as a **failure**, not as success, because the two
mistakes are not symmetric: a false failure leaves the item pending and re-sends
it next run (a duplicate message, recoverable), whereas a false success marks
content delivered that never arrived (lost, not recoverable). The freeze is
explicit that losing content is the one outcome it does not tolerate.

Deliberately deferred to a later version
----------------------------------------
* **No image segments.** Sending a thumbnail makes delivery depend on OneBot
  being able to fetch a third-party URL, which turns a reliable text path into an
  unreliable one. The canonical link is always present instead.
* **No internal retry.** Telegram can be retried precisely because it returns
  ``parameters.retry_after``; OneBot returns no such signal, so a retry would be
  a guess. A failure stays pending and the next run tries again.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from domain.models.item import NormalizedItem
from outputs.base import DeliveryResult
from outputs.base import MAX_CONSECUTIVE_FAILURES
from outputs.base import OutputAdapter
from outputs.base import record_batch_failure
from outputs.base import redact

logger = logging.getLogger("x_reader.outputs.qq")

#: The adapter's stable id — also its delivery-state namespace in storage.
OUTPUT_NAME = "qq"

DEFAULT_API_BASE = "http://127.0.0.1:3000"
DEFAULT_TIMEOUT = 20

#: OneBot v11 action for a group message.
ACTION_SEND_GROUP_MSG = "/send_group_msg"

#: The success code OneBot returns for a completed action.
RETCODE_OK = 0

#: Bounds on the two free-text fields.
#:
#: A QQ text message has a practical size limit that OneBot implementations
#: report inconsistently, and an item that can never be sent is worse than an
#: excerpt: it would stay pending forever and be retried on every run. Bounding
#: the two unbounded inputs keeps the whole message comfortably inside any
#: implementation's limit. Nothing is unreachable as a result — the canonical URL
#: is always part of the message, so a truncated excerpt costs convenience, not
#: access.
MAX_TITLE_CHARS = 200
MAX_CONTENT_CHARS = 600
TRUNCATION_MARKER = "…（已截断）"

#: A group id is a number; OneBot's spec types it as int64. A config value that
#: is all digits (optionally signed) is therefore sent as an integer, which is
#: what strict implementations expect, and anything else is passed through as a
#: string rather than being silently mangled.
_INTEGER_RE = re.compile(r"^[+-]?\d+$")


class QQOutput(OutputAdapter):
    """Send normalized items to a QQ group through a OneBot HTTP endpoint.

    Constructed even when the target is missing, for the same reason
    :class:`~outputs.telegram.TelegramOutput` is: a deployment that enables QQ
    without configuring it must fail *loudly at delivery time and keep the items
    pending*, not crash the run or silently drop content.
    """

    name = OUTPUT_NAME

    def __init__(
        self,
        *,
        api_base: str = DEFAULT_API_BASE,
        group_id: str = "",
        access_token: str = "",
        timeout: int = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_base = (api_base or DEFAULT_API_BASE).strip().rstrip("/")
        self.group_id = str(group_id or "").strip()
        self.access_token = (access_token or "").strip()
        # A plain number, not read back off the client: httpx exposes
        # ``Client.timeout`` as a ``Timeout`` object, which is not formattable.
        self.timeout = timeout
        self._owned_client = client is None
        # Injected transport is the test seam: status/retcode mapping and request
        # construction are the parts worth testing, and neither can be exercised
        # honestly without a OneBot instance.
        self._client = client or httpx.Client(timeout=timeout, transport=transport)

    # -- capability ---------------------------------------------------------
    @property
    def configured(self) -> bool:
        """True when there is somewhere to send to. A token is not required."""
        return bool(self.api_base and self.group_id)

    def unavailable_reason(self) -> str:
        """Why this adapter cannot send. Never contains a credential value."""
        missing = []
        if not self.api_base:
            missing.append("api base")
        if not self.group_id:
            missing.append("group id")
        if not missing:
            return ""
        return (
            f"qq is enabled but {' and '.join(missing)} is missing; "
            "set it in config/config.yaml (neither value is a secret)"
        )

    # -- rendering ----------------------------------------------------------
    def render(self, item: NormalizedItem) -> str:
        """One item as one plain-text QQ message.

        Plain text on purpose: OneBot's rich form is a CQ-code string, and
        emitting CQ codes from a renderer is how a feed title ends up being
        interpreted as a mention. Formatting lives here and touches nothing in
        ``NormalizedItem``.
        """
        blocks: list[str] = []

        if item.title:
            blocks.append(f"【{self._clip(item.title, MAX_TITLE_CHARS)}】")

        if item.content:
            blocks.append(self._clip(item.content, MAX_CONTENT_CHARS))

        meta: list[str] = []
        if item.publisher:
            meta.append(f"来源：{item.publisher}")
        # ``published_at`` is the source's claim; when it is absent the source's
        # own rendering is shown verbatim rather than inventing a time (DR-13).
        if item.published_at:
            meta.append(f"时间：{item.published_at}")
        elif item.published_at_raw:
            meta.append(f"时间：{item.published_at_raw}（原始值）")
        if item.canonical_url:
            meta.append(f"链接：{item.canonical_url}")
        if meta:
            blocks.append("\n".join(meta))

        return "\n\n".join(block for block in blocks if block)

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        """Collapse whitespace and bound the length, marking a real truncation."""
        collapsed = " ".join(text.split())
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[:limit] + TRUNCATION_MARKER

    # -- delivery -----------------------------------------------------------
    def emit(self, items: list[NormalizedItem]) -> DeliveryResult:
        """Deliver ``items``. Never raises: one bad item must not lose the rest."""
        result = DeliveryResult(output=self.name)
        if not items:
            return result

        reason = self.unavailable_reason()
        if reason:
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

            ok, detail = self._send(message, item.identity_key)
            if ok:
                result.record(item.identity_key, ok=True)
                consecutive_failures = 0
            else:
                result.record(item.identity_key, ok=False, detail=detail)
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._abort_batch(result, items[position + 1:], consecutive_failures)
                    return result

        return result

    def _abort_batch(
        self, result: DeliveryResult, remaining: list[NormalizedItem], failures: int
    ) -> None:
        """Give up on the rest of the batch, leaving every item pending.

        Reached only after a run of consecutive failures, which means the problem
        is the connection or the endpoint, not the content.
        """
        reason = (
            f"aborted after {failures} consecutive failures; "
            f"{len(remaining)} item(s) left pending for the next run"
        )
        logger.error("%s: %s", self.name, reason)
        record_batch_failure(result, remaining, reason)

    def _send(self, text: str, identity_key: str) -> tuple[bool, str]:
        """Send one message. Returns ``(ok, redacted_detail)``; never raises."""
        url = f"{self.api_base}{ACTION_SEND_GROUP_MSG}"
        payload = {
            "group_id": self._group_id_value(),
            # A single text segment, so the content is literal and no CQ code in
            # it can be interpreted (see the module docstring).
            "message": [{"type": "text", "data": {"text": text}}],
        }
        headers = self._headers()

        try:
            response = self._client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            return False, self._safe(f"timeout after {self.timeout}s: {exc}")
        except httpx.TransportError as exc:
            return False, self._safe(f"connection error: {exc}")
        except Exception as exc:  # noqa: BLE001 - a client bug must not kill the run
            return False, self._safe(f"unexpected error: {exc!r}")

        return self._interpret(response)

    def _interpret(self, response: httpx.Response) -> tuple[bool, str]:
        """Turn a OneBot response into ``(ok, detail)``.

        Success requires an explicit ``retcode == 0`` on a 2xx response. Anything
        else — including a 2xx whose body cannot be read — is a failure, because
        the two possible mistakes are not equally costly (see the module
        docstring).
        """
        status = response.status_code
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON body is a failure, not a success
            return False, self._safe(f"HTTP {status}: response was not JSON")

        if not isinstance(body, dict):
            return False, self._safe(f"HTTP {status}: malformed response")

        retcode = body.get("retcode")
        if 200 <= status < 300 and retcode == RETCODE_OK:
            return True, ""

        message = body.get("msg") or body.get("wording") or body.get("message") or ""
        detail = f"HTTP {status}"
        if retcode is not None:
            detail += f", retcode={retcode}"
        if message:
            detail += f": {message}"
        elif status >= 300 or retcode is None:
            detail += ": no OneBot error detail"
        return False, self._safe(detail)

    # -- helpers ------------------------------------------------------------
    def _group_id_value(self) -> Any:
        """An int when the group id is numeric, else the string as given."""
        if _INTEGER_RE.match(self.group_id):
            return int(self.group_id)
        return self.group_id

    def _headers(self) -> dict[str, str]:
        """Bearer auth, only when a token is configured."""
        if not self.access_token:
            return {}
        return {"Authorization": f"Bearer {self.access_token}"}

    def _safe(self, text: str) -> str:
        """Redact anything that could carry the token out of a message."""
        return redact(text, self.access_token)

    def close(self) -> None:
        """Close the client this adapter owns. Never raises."""
        if self._owned_client:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


def resolve_access_token(env_name: str, env: dict | None = None) -> str:
    """Read the OneBot access token from the environment by its configured name.

    Only the *name* lives in ``config.yaml``; the value never does. A blank name,
    an unset variable or an empty value all return ``""``, which means "send no
    Authorization header" — a legitimate OneBot configuration rather than an
    error.
    """
    if not env_name:
        return ""
    source = os.environ if env is None else env
    return (source.get(env_name) or "").strip()
