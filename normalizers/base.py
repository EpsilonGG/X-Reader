"""Normalizer contract.

Boundary (freeze v3, section 10): a Normalizer is the only component that knows
both a source's raw shape and the unified contract. It is a **pure function**:

* no network I/O;
* no Storage;
* no RSS rendering, no Telegram, no QQ, no Markdown, no AI;
* no knowledge of which provider or route produced the bytes.

Its single job is: source-specific raw record -> ``NormalizedItem``.

Why there is a base class at all
--------------------------------
Only because the validity gate is shared. Every source must apply the *same*
rule for "this record cannot become a valid item" — dropping it, rather than
letting an identity-less record reach Storage. Centralising that is the whole
reason this file exists; there is deliberately no plugin machinery, no
registration decorator and no config surface here.
"""
from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from domain.models.item import NormalizedItem


class BaseNormalizer(ABC):
    """Turns source-specific raw records into the unified contract."""

    #: Stable identifier, used for logging and the kind->normalizer registry.
    name: str = "base"

    def normalize(self, raw_items: list) -> list[NormalizedItem]:
        """Normalize a batch, keeping only items that satisfy the contract.

        ``normalize_one`` is allowed to return ``None`` (the record is not a
        content item at all) and is allowed to return an incomplete item (the
        source genuinely lacked a field). Anything that fails the contract's
        own validity rule is dropped here, so Storage never has to guess — but
        the caller can still see the loss by comparing input and output counts.
        """
        items: list[NormalizedItem] = []
        for raw in raw_items:
            item = self.normalize_one(raw)
            if item is not None and item.is_valid():
                items.append(item)
        return items

    @abstractmethod
    def normalize_one(self, raw) -> NormalizedItem | None:
        """Normalize a single raw record, or return ``None`` to discard it."""
        raise NotImplementedError
