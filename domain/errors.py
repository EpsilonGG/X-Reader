"""X-Reader error taxonomy.

The architecture freeze requires that failures be distinguishable by kind so
that a single account's failure never destroys other accounts' data, and so
that the run report can tell an operator *what* went wrong.

Taxonomy (mirrors the freeze, section 17):

    XReaderError
    ├── ConfigurationError   bad config / accounts file / missing setting
    ├── ProviderError        the fetch layer failed
    │   └── NetworkError     transport-level failure (timeout, DNS, 5xx)
    ├── ParsingError         a response arrived but could not be understood
    ├── StorageError         persisting the parsed data failed
    └── AllProvidersFailed   every configured provider failed for one account

Every error carries a stable machine-readable ``kind`` so log lines and the
run report can be parsed without string matching.
"""
from __future__ import annotations


class XReaderError(Exception):
    """Base class for every X-Reader error."""

    kind = "error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message or self.kind


class ConfigurationError(XReaderError):
    """Configuration could not be read or is not usable."""

    kind = "configuration_error"


class ProviderError(XReaderError):
    """The provider failed to return a usable raw response.

    ``status`` carries the HTTP status code when the failure came from an
    upstream response, which lets a provider distinguish "this account does not
    exist" (404 — stop trying other instances) from "this instance is refusing
    me" (403/429 — try the next instance).
    """

    kind = "provider_error"

    def __init__(self, message: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NetworkError(ProviderError):
    """Transport-level failure: timeout, connection error, 5xx, 429."""

    kind = "network_error"


class ParsingError(XReaderError):
    """A raw response was obtained but could not be parsed into tweets."""

    kind = "parsing_error"


class StorageError(XReaderError):
    """Persisting data failed."""

    kind = "storage_error"


class AllProvidersFailed(XReaderError):
    """Every provider/route combination failed for a single account.

    Carries the per-provider causes so the report can show why.
    """

    kind = "all_providers_failed"

    def __init__(self, account: str, causes: dict[str, str] | None = None) -> None:
        self.account = account
        self.causes = dict(causes or {})
        detail = "; ".join(f"{k}={v}" for k, v in self.causes.items()) or "no attempts"
        super().__init__(f"all providers failed for @{account}: {detail}")
