"""A tracked X account.

Accounts come from ``accounts.yaml`` and are never hardcoded in business
logic (architecture freeze, section 16).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Account:
    """One X account that X-Reader tracks over time."""

    username: str
    enabled: bool = True

    def __post_init__(self) -> None:
        cleaned = self.username.strip().lstrip("@")
        if not cleaned:
            raise ValueError("account username must not be empty")
        object.__setattr__(self, "username", cleaned)

    @property
    def display(self) -> str:
        return f"@{self.username}"
