"""Configuration models.

Sectioning and naming follow X-rss (`config/schema.py`): a top-level
``provider`` / ``http`` block plus typed sub-models with path properties.
Pydantic is used here on purpose — X-rss's convention, and validation is
exactly what a config layer should do.

Two behaviours are deliberately stricter than X-rss:

* endpoint strings are whitespace-stripped during validation. X-rss's live
  ``config.yaml`` has six endpoints that *look* like they carry a trailing
  space (``cat -A`` shows two spaces), but they are unquoted YAML plain
  scalars and YAML strips leading/trailing whitespace from those. Loading
  X-rss's file with ``yaml.safe_load`` returns all 14 endpoints clean, so this
  stripping is **defensive hardening, not a fix for a real defect** — it makes
  the whole class of bug impossible rather than repairing an existing one.
* unknown provider names and empty endpoint lists are rejected at load time
  instead of surfacing as a mysterious all-accounts failure at run time.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from domain.models.item import RESERVED_SOURCE_IDS

#: Provider names X-Reader knows how to build for X accounts.
KNOWN_PROVIDERS = ("nitter", "xtf")

#: Nitter routes: the RSS feed and the HTML timeline page.
KNOWN_NITTER_ROUTES = ("rss", "html")

#: A source id must be a lowercase slug: stable, filesystem-safe, and usable as
#: an identity namespace (see RssSourceConfig).
_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

#: A YouTube channel id is ``UC`` followed by 22 characters. Not enforced to the
#: letter — a stricter format would reject a legitimate future change — but the
#: character set is, so a pasted URL or a display name is caught at load time
#: rather than becoming an unaddressable source id at run time.
_CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")

RawResponsePolicy = Literal["never", "on_error", "always"]


class HTTPConfig(BaseModel):
    timeout: int = Field(default=20, gt=0)
    retries: int = Field(default=1, ge=0)


class NitterProviderConfig(BaseModel):
    endpoints: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=lambda: list(KNOWN_NITTER_ROUTES))

    @field_validator("endpoints")
    @classmethod
    def _clean_endpoints(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            endpoint = (raw or "").strip().rstrip("/")
            if endpoint and endpoint not in cleaned:
                cleaned.append(endpoint)
        return cleaned

    @field_validator("routes")
    @classmethod
    def _known_routes(cls, value: list[str]) -> list[str]:
        unknown = [r for r in value if r not in KNOWN_NITTER_ROUTES]
        if unknown:
            raise ValueError(
                f"unknown nitter route(s): {unknown}; known: {list(KNOWN_NITTER_ROUTES)}"
            )
        if not value:
            raise ValueError("at least one nitter route is required")
        return value


class XtfProviderConfig(BaseModel):
    """Configuration for the x-tweet-fetcher adapter.

    ``instances`` is intentionally allowed to be empty: when empty the adapter
    falls back to the Nitter endpoints, so there is a single source of truth
    for instances.
    """

    enabled: bool = True
    instances: list[str] = Field(default_factory=list)
    route: str = "search"

    @field_validator("instances")
    @classmethod
    def _clean_instances(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            instance = (raw or "").strip().rstrip("/")
            if instance and instance not in cleaned:
                cleaned.append(instance)
        return cleaned


class ProviderConfig(BaseModel):
    """The X fallback chain.

    ``order`` may be **empty**, which means "X input is disabled". That is a
    legitimate Phase 2 configuration: X and RSS are independent input sources,
    so a deployment that only reads feeds must not be forced to declare an X
    provider (and, with it, a Nitter endpoint list it will never use).

    An empty ``order`` is *not* by itself a valid configuration — ``Config``
    additionally requires at least one enabled input source overall, so
    "no X and no feeds" is still rejected. The rule simply moved from "you must
    have X" to "you must have something".
    """

    order: list[str] = Field(default_factory=lambda: ["nitter", "xtf"])
    nitter: NitterProviderConfig = Field(default_factory=NitterProviderConfig)
    xtf: XtfProviderConfig = Field(default_factory=XtfProviderConfig)

    @model_validator(mode="after")
    def _validate_order(self) -> ProviderConfig:
        unknown = [p for p in self.order if p not in KNOWN_PROVIDERS]
        if unknown:
            raise ValueError(
                f"unknown provider(s) in provider.order: {unknown}; "
                f"known: {list(KNOWN_PROVIDERS)}"
            )
        # Only enforced when Nitter is actually in the chain: a config that
        # never uses it must not have to supply endpoints. This is the
        # X-specific validation the task asks to preserve untouched.
        if "nitter" in self.order and not self.nitter.endpoints:
            raise ValueError(
                "provider.nitter.endpoints must not be empty "
                "(nitter is listed in provider.order)"
            )
        return self


class FetchConfig(BaseModel):
    """How much to keep per account per run."""

    limit: int = Field(default=20, gt=0)


class StorageConfig(BaseModel):
    data_dir: str = "data"
    #: never | on_error | always — controls raw response body capture.
    store_raw_response: RawResponsePolicy = "on_error"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def items_dir(self) -> Path:
        """Canonical NormalizedItem store, one file per source."""
        return self.data_path / "items"

    @property
    def accounts_dir(self) -> Path:
        """Raw record archive (Phase 1 name; X tweets only today)."""
        return self.data_path / "accounts"

    @property
    def runs_dir(self) -> Path:
        return self.data_path / "runs"

    @property
    def raw_dir(self) -> Path:
        return self.data_path / "raw"

    @property
    def delivery_dir(self) -> Path:
        """Per-output delivery state — separate from "seen" on purpose."""
        return self.data_path / "delivery"


class RssSourceConfig(BaseModel):
    """One external RSS/Atom feed X-Reader reads.

    ``id`` is the **identity namespace** for everything that feed produces
    (freeze v3 section 6). It must therefore be:

    * explicit — never derived from a display name at runtime, because a renamed
      site would silently fork every item's identity;
    * stable — changing it after data has been stored re-identifies the whole
      feed;
    * not a reserved name (``x`` / ``youtube``), so ``identity_key`` stays
      unambiguous.
    """

    id: str
    url: str
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _clean_id(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("rss source id must not be empty")
        if cleaned in RESERVED_SOURCE_IDS:
            raise ValueError(
                f"rss source id '{cleaned}' is reserved; "
                f"reserved names: {list(RESERVED_SOURCE_IDS)}"
            )
        if not _SOURCE_ID_RE.match(cleaned):
            raise ValueError(
                f"rss source id '{cleaned}' must be a slug of "
                f"[a-z0-9._-] (lowercase) so it stays stable and filesystem-safe"
            )
        return cleaned

    @field_validator("url")
    @classmethod
    def _clean_url(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("rss source url must not be empty")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError(f"rss source url must be http(s), got '{cleaned}'")
        return cleaned


class YoutubeChannelConfig(BaseModel):
    """One YouTube channel X-Reader follows (Phase 4).

    Deliberately minimal, and deliberately *not* shaped like
    :class:`RssSourceConfig`: a YouTube channel has no configurable identity of
    its own. The ``channel_id`` **is** the identity, because it is the one thing
    about a channel that never changes — a display name can be renamed at any
    time, and deriving an identity from it would fork every stored item the
    moment the operator renamed the channel.

    ``url`` exists only as an escape hatch for a self-hosted mirror or a test
    double. Left empty — the normal case — the provider generates the public
    feed URL from ``channel_id``, so the operator supplies one value, not two
    that can disagree.
    """

    #: The ``UC...`` channel id. Stable, globally unique, never a display name.
    channel_id: str
    #: Optional feed URL override; empty means "generate it from channel_id".
    url: str = ""
    enabled: bool = True

    @field_validator("channel_id")
    @classmethod
    def _clean_channel_id(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("youtube channel_id must not be empty")
        if cleaned.startswith(("http://", "https://")) or "/" in cleaned:
            raise ValueError(
                f"youtube channel_id '{cleaned}' looks like a URL; supply the "
                f"channel id itself (the UC... value), not a link"
            )
        if not _CHANNEL_ID_RE.match(cleaned):
            raise ValueError(
                f"youtube channel_id '{cleaned}' must be [A-Za-z0-9_-] only "
                f"(no spaces, no @handle, no display name)"
            )
        return cleaned

    @field_validator("url")
    @classmethod
    def _clean_url(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if cleaned and not cleaned.startswith(("http://", "https://")):
            raise ValueError(f"youtube url must be http(s), got '{cleaned}'")
        return cleaned


class TelegramConfig(BaseModel):
    """Telegram delivery (Phase 3).

    This block carries only the **names** of the environment variables that hold
    the two secrets. The values live in the environment — a shell variable
    locally, a GitHub repository secret in CI — and are never written to
    ``config.yaml``, ``data/``, a log line or a workflow artifact.

    ``extra="forbid"`` is a safety property, not tidiness. Pydantic ignores
    unknown keys by default, so without it a config that spelled
    ``bot_token: "123:ABC"`` would be *silently ignored* and the operator would
    see "telegram is enabled but bot token is missing" while staring at a token
    in the file. Forbidding unknown keys turns that into a start-up error — and
    makes it impossible to "make it work" by committing a secret.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"

    @field_validator("bot_token_env", "chat_id_env")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return (value or "").strip()

    @model_validator(mode="after")
    def _names_required_when_enabled(self) -> TelegramConfig:
        # Only enforced when the output is on: a disabled block must not have to
        # be complete, but an enabled one with no variable to read is a
        # misconfiguration that would otherwise fail later with a worse message.
        if self.enabled and not self.bot_token_env:
            raise ValueError("telegram.bot_token_env must name an environment variable")
        if self.enabled and not self.chat_id_env:
            raise ValueError("telegram.chat_id_env must name an environment variable")
        return self


class QQConfig(BaseModel):
    """QQ delivery over the OneBot HTTP API (Phase 4).

    Deliberately **not** the official QQ bot API, and deliberately not a QQ SDK:
    X-Reader posts one HTTP request per message to a OneBot implementation the
    operator already runs. X-Reader is a client of OneBot, never a QQ bot server,
    never a long-lived WebSocket client, and never a resident process — all three
    would violate the freeze's "free GitHub Actions, no VPS, no NAS" constraint.

    Four things here are easy to confuse, so they are named apart:

    * ``group_id`` — *where* the message goes. A number, not a secret.
    * ``api_base`` — *where OneBot listens*. Not a secret.
    * ``access_token_env`` — the **name** of the environment variable holding the
      OneBot access token. The token is a secret and must never be written into
      this file, which is committed.
    * the GitHub Secret itself — the value behind that variable name.

    ``extra="forbid"`` is the same safety property as in
    :class:`TelegramConfig`: it turns "I put my access token in config.yaml and it
    was silently ignored" into a start-up error, and makes committing a token
    impossible rather than merely discouraged.

    ``access_token_env`` is **not** required when enabled, unlike Telegram's
    variables. A OneBot deployment with no access token configured is a normal,
    supported setup; requiring the operator to name a variable they do not have
    would only encourage a placeholder.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    #: OneBot HTTP API root, e.g. ``http://127.0.0.1:3000``. No trailing slash.
    api_base: str = "http://127.0.0.1:3000"
    #: Target group. Accepts a number in YAML; always handled as a string.
    group_id: str = ""
    #: Name of the env var holding the OneBot access token. Empty = send no token.
    access_token_env: str = "ONEBOT_ACCESS_TOKEN"
    timeout: int = Field(default=20, gt=0)

    @field_validator("api_base")
    @classmethod
    def _clean_api_base(cls, value: str) -> str:
        cleaned = (value or "").strip().rstrip("/")
        if cleaned and not cleaned.startswith(("http://", "https://")):
            raise ValueError(
                f"qq.api_base must be an http(s) URL, got '{cleaned}'"
            )
        return cleaned

    @field_validator("group_id", mode="before")
    @classmethod
    def _clean_group_id(cls, value) -> str:
        # YAML writes a QQ group id as a bare number, and pydantic v2 does not
        # coerce int to str. Accepting both here means `group_id: 123456` and
        # `group_id: "123456"` behave identically, instead of the unquoted form
        # failing with a confusing type error.
        if value is None:
            return ""
        if isinstance(value, bool):
            raise ValueError("qq.group_id must be a number or a string, not a boolean")
        if isinstance(value, int):
            return str(value)
        return (str(value)).strip()

    @field_validator("access_token_env")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return (value or "").strip()

    @model_validator(mode="after")
    def _required_when_enabled(self) -> QQConfig:
        # Only enforced when the output is on: a disabled block must not have to
        # be complete, but an enabled one with no target is a misconfiguration
        # that would otherwise fail later with a worse message.
        if self.enabled and not self.api_base:
            raise ValueError("qq.api_base must not be empty when qq is enabled")
        if self.enabled and not self.group_id:
            raise ValueError("qq.group_id must not be empty when qq is enabled")
        return self


class Config(BaseModel):
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    http: HTTPConfig = Field(default_factory=HTTPConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    #: Downstream delivery. Independent of the input sources: a deployment can
    #: acquire without delivering, and delivery never affects what is fetched.
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    #: Second delivery target, in its own configuration space. Telegram and QQ
    #: are independent: enabling one must never change the other's behaviour,
    #: and each keeps its own delivery state (``data/delivery/<output>.jsonl``).
    qq: QQConfig = Field(default_factory=QQConfig)
    #: External RSS/Atom feeds. Kept in a separate configuration space from
    #: ``accounts``: an RSS source is not an X account, and pretending otherwise
    #: is how the two get tangled.
    rss_sources: list[RssSourceConfig] = Field(default_factory=list)
    #: YouTube channels, again a separate space. A channel is not a feed URL and
    #: not an X account: its identity is its ``channel_id``.
    youtube_channels: list[YoutubeChannelConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_input_sources(self) -> Config:
        """Reject ids that collide, and reject having nothing to fetch.

        The rule this replaces was "``provider.order`` must not be empty" — a
        Phase 1 assumption that X was the only possible input. Phase 2 made RSS
        an independent input source, so the honest rule is about *inputs*, not
        about X:

        * ``provider.order`` non-empty → there is an X input; fine.
        * ``provider.order`` empty → X is disabled, so at least one **enabled**
          RSS source or YouTube channel is required.
        * neither → nothing could ever be fetched, which is still an error.

        The rule generalises by *counting inputs*, not by listing sources: each
        new input family adds one clause to the same predicate, and no family is
        privileged. The X-specific rules are untouched: ``nitter`` in
        ``provider.order`` still requires endpoints (see :class:`ProviderConfig`),
        so allowing RSS-only or YouTube-only does not weaken X validation.
        """
        seen: set[str] = set()
        for source in self.rss_sources:
            key = source.id.lower()
            if key in seen:
                raise ValueError(f"duplicate rss source id: '{source.id}'")
            seen.add(key)

        channels: set[str] = set()
        for channel in self.youtube_channels:
            key = channel.channel_id.lower()
            if key in channels:
                raise ValueError(
                    f"duplicate youtube channel_id: '{channel.channel_id}'"
                )
            channels.add(key)

        if (
            not self.provider.order
            and not self.enabled_rss_sources
            and not self.enabled_youtube_channels
        ):
            raise ValueError(
                "no input source configured: provider.order is empty (X input "
                "disabled), no rss_sources are enabled and no youtube_channels "
                "are enabled; at least one input source is required"
            )
        return self

    @property
    def enabled_rss_sources(self) -> list[RssSourceConfig]:
        return [source for source in self.rss_sources if source.enabled]

    @property
    def enabled_youtube_channels(self) -> list[YoutubeChannelConfig]:
        return [channel for channel in self.youtube_channels if channel.enabled]

    @property
    def x_input_enabled(self) -> bool:
        """True when ``provider.order`` declares at least one X provider."""
        return bool(self.provider.order)

    @property
    def youtube_input_enabled(self) -> bool:
        """True when at least one YouTube channel is enabled."""
        return bool(self.enabled_youtube_channels)
