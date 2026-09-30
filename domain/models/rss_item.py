"""RawRSSItem — one entry of a generic RSS/Atom feed, as parsed.

This is the *raw* side of the RSS/web source family: a projection of what the
feed actually said, with no attempt to unify it with X or YouTube. The
Normalizer is what turns it into a ``NormalizedItem``.

Scope note (Phase 2, section 1 of the task): X-Reader supports a **generic**
RSS/XML input. It has no ``VisualNovelProvider`` and does not import anything
from ``VisualNovel-Interview-RSS-main``; that project's role is simply to emit
standard RSS, which any other project may also do.

Field justification — every field here is something RSS 2.0 / Atom actually
carries and that the Normalizer needs:

* ``source_id`` — the identity namespace, taken from the RSS source config.
  Without it, two feeds could not be told apart (freeze section 6).
* ``item_id_raw`` — ``<guid>`` / ``<id>`` verbatim. Empty when the feed omits it,
  which is the common case in the wild.
* ``title`` / ``summary`` / ``content`` — the two content slots RSS distinguishes
  (``<description>`` vs ``<content:encoded>``).
* ``link`` — the human-facing URL.
* ``published_raw`` / ``published_at`` / ``published_precision`` — the source's
  own rendering, plus the best interpretation of it. Kept apart on purpose: a
  date-only value must never be silently promoted to a precise instant.
* ``updated_raw`` — kept as **evidence only**. The contract has no ``updated_at``
  field (Decision Record DR-4: no source in this workspace exposes an item-level
  update time), so the Normalizer does not propagate it. Dropping it here would
  destroy information that cannot be refetched.
* ``author`` / ``feed_title`` — the two candidates for ``publisher``. RSS has an
  item-level ``<author>`` and a feed-level title; neither is guaranteed.
* ``enclosure_*`` — RSS ``<enclosure>``, the only media RSS 2.0 defines.
* ``categories`` — ``<category>`` values, carried as metadata.
* ``fetched_at`` — when we observed it (never a substitute for ``published_at``).
* ``raw`` — the parser's verbatim extraction, so a future Normalizer can
  re-derive a better model without refetching.
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field


@dataclass(slots=True)
class RawRSSItem:
    """One feed entry, before normalization."""

    # --- identity -----------------------------------------------------------
    #: The RSS source config id this entry came from (e.g. ``"visualnovel-interview"``).
    source_id: str
    #: ``<guid>`` / ``<id>`` verbatim; empty when the feed has none.
    item_id_raw: str = ""

    # --- content ------------------------------------------------------------
    title: str = ""
    summary: str = ""
    content: str = ""
    link: str = ""

    # --- publication time ---------------------------------------------------
    published_raw: str = ""
    published_at: str | None = None
    published_precision: str = "unknown"

    # --- feed-level / item-level provenance ---------------------------------
    #: Item-level ``<author>`` / Atom ``<author><name>``, if present.
    author: str = ""
    #: Feed title (``<channel><title>`` / Atom ``<feed><title>``).
    feed_title: str = ""

    # --- media --------------------------------------------------------------
    enclosure_url: str = ""
    enclosure_type: str = ""
    enclosure_length: str = ""

    # --- extras -------------------------------------------------------------
    categories: list[str] = field(default_factory=list)
    #: Atom ``<updated>``. Evidence only — see the module docstring.
    updated_raw: str = ""

    # --- observation --------------------------------------------------------
    fetched_at: str = ""

    # --- evidence -----------------------------------------------------------
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "item_id_raw": self.item_id_raw,
            "title": self.title,
            "summary": self.summary,
            "content": self.content,
            "link": self.link,
            "published_raw": self.published_raw,
            "published_at": self.published_at,
            "published_precision": self.published_precision,
            "author": self.author,
            "feed_title": self.feed_title,
            "enclosure_url": self.enclosure_url,
            "enclosure_type": self.enclosure_type,
            "enclosure_length": self.enclosure_length,
            "categories": list(self.categories),
            "updated_raw": self.updated_raw,
            "fetched_at": self.fetched_at,
            "raw": self.raw,
        }
