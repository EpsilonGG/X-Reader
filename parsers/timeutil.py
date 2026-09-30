"""Date handling shared by parsers.

Why this exists as its own module: timestamps are the single most fragile part
of scraping Nitter, they are shared by two parsers, and they are the one place
where it is tempting to invent data. The rule enforced here is simple:

    return an absolute timestamp when the source rendered one, otherwise None.

Never derive an absolute time from a relative one ("3h" is recorded verbatim in
``created_at_raw`` and ``created_at`` stays ``None``). Guessing would put a
fabricated timestamp into the raw store, and the whole point of Phase 1 storage
is that it holds facts, not inferences.
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime

#: Nitter renders tweet dates in an anchor title, e.g.
#:   "Sep 11, 2024 · 7:31 PM UTC"
#: The separator is a middle dot surrounded by spaces.
_NITTER_TITLE_FORMATS = (
    "%b %d, %Y %I:%M %p %Z",   # Sep 11, 2024 7:31 PM UTC
    "%B %d, %Y %I:%M %p %Z",   # September 11, 2024 7:31 PM UTC
    "%b %d, %Y %I:%M %p",      # Sep 11, 2024 7:31 PM   (no zone)
    "%B %d, %Y %I:%M %p",
)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_source_date(raw: str) -> str | None:
    """Parse an absolute timestamp rendered by a source into ISO-8601 UTC.

    Returns ``None`` when the input is empty, relative ("3h", "Jul 3") or in an
    unrecognised format.
    """
    text = (raw or "").strip()
    if not text:
        return None

    # 1. RFC 2822 — what Nitter's RSS <pubDate> uses.
    try:
        return _to_utc(parsedate_to_datetime(text)).isoformat()
    except (TypeError, ValueError):
        pass

    # 2. Nitter's HTML anchor title format.
    normalized = " ".join(text.replace("·", " ").split())
    for fmt in _NITTER_TITLE_FORMATS:
        try:
            return _to_utc(datetime.strptime(normalized, fmt)).isoformat()
        except ValueError:
            continue

    # 3. ISO 8601, tolerating a trailing Z.
    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return _to_utc(datetime.fromisoformat(iso_candidate)).isoformat()
    except ValueError:
        return None


def is_absolute_date(raw: str) -> bool:
    """Whether :func:`parse_source_date` would succeed on ``raw``."""
    return parse_source_date(raw) is not None
