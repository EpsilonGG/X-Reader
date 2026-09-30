"""HTTP transport: the single choke point for every upstream request.

Lineage:

* X-rss (`infrastructure/http/client.py`) — one small ``httpx.Client`` wrapper,
  fixed browser-like User-Agent, ``RawResponse`` on success. That shape is kept.
* x-tweet-fetcher (`http.py`) — retry-with-backoff for *transient* failures
  only, a never-raising reachability probe, and typed errors instead of
  tri-state returns. That behaviour is adopted here because X-Reader runs on
  datacenter IPs in GitHub Actions, where 429/5xx from public Nitter instances
  is the normal case rather than the exception.

What X-rss did *not* do, and why it matters here: X-rss calls
``raise_for_status()`` and lets any 4xx/5xx become a generic exception. X-Reader
needs to tell "the instance is rate-limiting me" apart from "this account does
not exist", so status codes are mapped onto the error taxonomy.
"""
from __future__ import annotations

import time
from datetime import datetime
from datetime import timezone

import httpx

from domain.errors import NetworkError
from domain.errors import ProviderError
from infrastructure.http.response import RawResponse

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 "
    "(Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/138.0 Safari/537.36"
)

#: Status codes worth retrying: rate limiting and transient upstream failure.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 522, 524})

#: Status codes that mean "this request will never succeed as written".
FATAL_STATUS = frozenset({400, 401, 403, 404, 410, 451})


class HTTPClient:
    """Minimal HTTP GET client with bounded retries and typed failures."""

    def __init__(
        self,
        timeout: int = 20,
        user_agent: str = DEFAULT_USER_AGENT,
        retries: int = 1,
        backoff_base: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if retries < 0:
            raise ValueError("retries must be >= 0")
        self.timeout = timeout
        self.retries = retries
        self.backoff_base = backoff_base
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
            # Injection seam for tests. Retry/backoff and status-code mapping are
            # the parts of this class most worth testing, and neither can be
            # exercised honestly against the live network.
            transport=transport,
        )

    # -- public API ---------------------------------------------------------
    def get(self, url: str, *, headers: dict[str, str] | None = None) -> RawResponse:
        """GET ``url`` and return a :class:`RawResponse`.

        Raises :class:`NetworkError` for retryable/transport failures and
        :class:`ProviderError` for fatal status codes.
        """
        last_error: NetworkError | None = None

        for attempt in range(self.retries + 1):
            try:
                response = self._client.get(url, headers=headers)
            except httpx.TimeoutException:
                last_error = NetworkError(f"timeout after {self.timeout}s — {url}")
            except httpx.TransportError as exc:
                last_error = NetworkError(f"transport error — {url}: {exc}")
            else:
                status = response.status_code
                if status in FATAL_STATUS:
                    raise ProviderError(f"HTTP {status} — {url}", status=status)
                if status in RETRYABLE_STATUS or status >= 500:
                    last_error = NetworkError(f"HTTP {status} — {url}", status=status)
                elif status >= 400:
                    raise ProviderError(f"HTTP {status} — {url}", status=status)
                else:
                    return RawResponse(
                        url=str(response.url),
                        status_code=status,
                        content_type=response.headers.get("Content-Type", ""),
                        text=response.text,
                        fetched_at=datetime.now(timezone.utc),
                    )

            if attempt < self.retries:
                delay = self.backoff_base * (2**attempt)
                time.sleep(delay)

        assert last_error is not None
        raise last_error

    def probe(self, url: str, timeout: int = 3) -> bool:
        """Cheap reachability check. Never raises — returns False on failure."""
        try:
            response = self._client.get(url, timeout=timeout)
        except Exception:
            return False
        return response.status_code < 400

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HTTPClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
