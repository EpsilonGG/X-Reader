"""Configuration loading.

Shape inherited from X-rss (`config/loader.py`): relative default paths resolved
from the project root, plus a ``load_accounts()`` helper. Improved in two ways:

* failures raise :class:`ConfigurationError` with the offending path, instead of
  leaking ``FileNotFoundError`` / ``yaml.YAMLError`` to the caller;
* both paths are overridable, which is what makes the config layer testable.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from config.schema import Config
from domain.errors import ConfigurationError
from domain.models.account import Account

CONFIG_PATH = Path("config/config.yaml")
ACCOUNTS_PATH = Path("accounts.yaml")


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigurationError(f"configuration file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"{path} must contain a YAML mapping, got {type(data).__name__}")
    return data


def load_config(path: str | Path = CONFIG_PATH) -> Config:
    """Load and validate ``config/config.yaml``."""
    data = _read_yaml(Path(path))
    try:
        return Config(**data)
    except Exception as exc:  # pydantic.ValidationError and friends
        raise ConfigurationError(f"invalid configuration in {path}: {exc}") from exc


def load_accounts(path: str | Path = ACCOUNTS_PATH) -> list[Account]:
    """Load ``accounts.yaml``.

    Accepted forms::

        accounts:
          - username: jack
          - username: OpenAI
            enabled: false
          - jack            # bare string shorthand
        accounts: []        # no X accounts at all (RSS-only deployment)

    Disabled accounts are returned too, so callers can report them; filtering
    happens in the runner.

    An **empty list is valid** since Phase 2. X and RSS are independent input
    sources, so a deployment that only reads feeds has no X accounts to declare;
    requiring a dummy one would make "RSS-only" impossible to express. The
    safety net that this used to provide ("you forgot to configure accounts")
    now lives one level up and is stronger, because it is about inputs rather
    than about X specifically: ``Config`` requires at least one enabled input
    source, and ``main.py`` refuses to start when there is no unit to process.
    """
    data = _read_yaml(Path(path))
    raw_accounts = data.get("accounts")
    if raw_accounts is None:
        raise ConfigurationError(f"{path} is missing the 'accounts' key")
    if not isinstance(raw_accounts, list):
        raise ConfigurationError(f"{path}: 'accounts' must be a list")

    accounts: list[Account] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_accounts):
        if isinstance(item, str):
            payload: dict = {"username": item}
        elif isinstance(item, dict):
            payload = item
        else:
            raise ConfigurationError(
                f"{path}: accounts[{index}] must be a string or a mapping, "
                f"got {type(item).__name__}"
            )
        username = payload.get("username")
        if not username or not isinstance(username, str):
            raise ConfigurationError(f"{path}: accounts[{index}] is missing 'username'")
        try:
            account = Account(
                username=username,
                enabled=bool(payload.get("enabled", True)),
            )
        except ValueError as exc:
            raise ConfigurationError(f"{path}: accounts[{index}]: {exc}") from exc

        key = account.username.lower()
        if key in seen:
            raise ConfigurationError(f"{path}: duplicate account '{account.username}'")
        seen.add(key)
        accounts.append(account)

    return accounts
