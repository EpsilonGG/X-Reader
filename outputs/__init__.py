"""Output layer: downstream consumers of NormalizedItem.

Two adapters exist — Telegram (``telegram.py``, Phase 3) and QQ over OneBot
(``qq.py``, Phase 4) — and they are independent: either, both or neither can be
enabled, and neither imports the other. What they share is only the boundary
they both fit behind: an adapter receives normalized items, returns a
``DeliveryResult``, and never becomes a second source of truth for what has been
delivered.

Deliberately absent: an output manager, a universal adapter, an RSS/feed writer,
a Markdown renderer, and any summary or LLM call.
"""
from outputs.base import DeliveryResult
from outputs.base import OutputAdapter

__all__ = [
    "OutputAdapter",
    "DeliveryResult",
]
