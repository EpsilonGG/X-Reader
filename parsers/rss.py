"""Generic RSS 2.0 / Atom parser.

Deliberately generic: it knows nothing about any particular site, and nothing
about VisualNovel-Interview-RSS-main beyond the fact that it emits standard RSS.
X-Reader supports "any feed URL declared in ``rss_sources``", so this parser is
the whole of the RSS input capability.

Boundary (freeze, section 8): a parser understands bytes. No network, no storage,
no dedup, no output formatting. It returns :class:`RawRSSItem` records and stops.

Handled shapes
--------------
* RSS 2.0 — ``rss/channel/item`` with ``title``, ``link``, ``guid``,
  ``description``, ``pubDate``, ``enclosure``, ``category``, ``author``.
* Atom — ``feed/entry`` with ``title``, ``link[href]``, ``id``, ``summary``,
  ``content``, ``published``/``updated``, ``author/name``,
  ``link[rel=enclosure]``.
* Namespaced extensions that carry content or media — ``content:encoded`` and
  ``media:content`` — because feeds in the wild use them and dropping them would
  lose real data.

Time handling (freeze v3 section 7, Decision Record DR-13)
---------------------------------------------------------
The parser reports what the source said, and never invents a timezone:

* a value with an offset is normalised to UTC and reported at the precision the
  string actually carries;
* a **date-only** value is reported as ``YYYY-MM-DD`` — not as midnight;
* a naive date-and-time is reported without a zone, because we do not know the
  site's zone (see risk R2: a real upstream pipeline in this workspace parses
  Japanese local time and labels it UTC, an error of nine hours);
* anything unparseable leaves ``published_at`` empty with precision ``unknown``,
  keeping the source's own string in ``published_raw``.
"""
from __future__ import annotations

import re
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from domain.errors import ParsingError
from domain.models.account import Account
from domain.models.item import PRECISION_DAY
from domain.models.item import PRECISION_HOUR
from domain.models.item import PRECISION_MINUTE
from domain.models.item import PRECISION_SECOND
from domain.models.item import PRECISION_UNKNOWN
from domain.models.rss_item import RawRSSItem
from parsers.base import BaseParser

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT_ENCODED = "{http://purl.org/rss/1.0/modules/content/}encoded"
MEDIA_CONTENT = "{http://search.yahoo.com/mrss/}content"
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"

_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _text(element) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def parse_feed_time(raw: str) -> tuple[str | None, str]:
    """Interpret a feed's time string. Returns ``(iso_or_none, precision)``.

    Never fabricates a time or a zone — see the module docstring.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, PRECISION_UNKNOWN

    # --- RFC 822 ("Mon, 28 Sep 2026 11:45:00 +0000") -----------------------
    if "," in raw or re.match(r"^\d{1,2} \w{3} \d{4}", raw):
        try:
            moment = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            moment = None
        if moment is not None:
            precision = _precision_from_text(raw)
            if moment.tzinfo is None:
                # Zone genuinely absent from the string; do not assume one.
                return moment.isoformat(), precision
            return moment.astimezone(timezone.utc).isoformat(), precision

    # --- ISO 8601 ---------------------------------------------------------
    if _DATE_RE.match(raw):
        return raw, PRECISION_DAY
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, PRECISION_UNKNOWN
    precision = _precision_from_text(raw)
    if moment.tzinfo is None:
        return moment.isoformat(), precision
    return moment.astimezone(timezone.utc).isoformat(), precision


def _precision_from_text(raw: str) -> str:
    """Granularity actually present in the string, not the granularity we wish."""
    match = _TIME_RE.search(raw)
    if not match:
        return PRECISION_DAY if _DATE_RE.match(raw.strip()) else PRECISION_UNKNOWN
    if match.group(3) is not None:
        return PRECISION_SECOND
    if match.group(2) == "00":
        return PRECISION_HOUR
    return PRECISION_MINUTE


class RssParser(BaseParser):
    """Turns a feed document into :class:`RawRSSItem` records."""

    name = "rss"

    def parse(self, response, account: Account) -> list[RawRSSItem]:
        """Parse ``response`` into feed entries attributed to ``account``.

        ``account`` carries the tracked unit's identifier, which for an RSS
        source is its config id (``rss_sources[].id``). The Provider/Parser
        interface was frozen in Phase 1 and is deliberately not changed here.
        """
        body = self.require_text(response, self.name)
        source_id = account.username
        fetched_at = response.fetched_at.isoformat()

        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ParsingError(f"rss: not well-formed XML from {response.url}: {exc}") from exc

        channel = self._channel(root)
        if channel is None:
            raise ParsingError(
                f"rss: {response.url} is XML but not an RSS/Atom feed "
                f"(root <{root.tag}>)"
            )
        feed_title = _text(channel.find("title")) or _text(channel.find(f"{ATOM}title"))

        items: list[RawRSSItem] = []
        for entry in self._entries(channel):
            try:
                item = self._entry(entry, source_id=source_id, feed_title=feed_title,
                                   fetched_at=fetched_at)
            except Exception:  # noqa: BLE001 - one bad entry must not kill the feed
                continue
            if item is not None:
                items.append(item)
        return items

    # -- document shape -----------------------------------------------------
    @staticmethod
    def _channel(root):
        if root.tag == "rss":
            return root.find("channel")
        if root.tag == "feed" or root.tag == f"{ATOM}feed":
            return root
        # Tolerate a feed wrapped in RDF (RSS 1.0): channel metadata is optional
        # there, and entries are siblings of <channel>, handled by _entries.
        if root.tag.endswith("RDF"):
            return root
        return None

    @staticmethod
    def _entries(channel) -> list:
        if channel.tag == "rss":
            return channel.findall("item")
        if channel.tag.endswith("RDF"):
            return channel.findall("item")
        if channel.tag == "feed" or channel.tag == f"{ATOM}feed":
            return channel.findall(f"{ATOM}entry")
        return channel.findall("item")

    # -- one entry ----------------------------------------------------------
    def _entry(self, entry, *, source_id: str, feed_title: str, fetched_at: str) -> RawRSSItem | None:
        link = self._link(entry)
        published_raw, updated_raw = self._times(entry)
        published_at, precision = parse_feed_time(published_raw)
        if published_at is None and updated_raw:
            # Atom's <updated> is the only time some feeds give. Used as a
            # fallback, never promoted into a separate contract field (DR-4).
            published_at, precision = parse_feed_time(updated_raw)

        enclosure_url, enclosure_type, enclosure_length = self._enclosure(entry)

        item = RawRSSItem(
            source_id=source_id,
            item_id_raw=self._id(entry),
            title=self._title(entry),
            summary=_text(entry.find("description")) or _text(entry.find(f"{ATOM}summary")),
            content=_text(entry.find(CONTENT_ENCODED)) or _text(entry.find(f"{ATOM}content")),
            link=link,
            published_raw=published_raw,
            published_at=published_at,
            published_precision=precision,
            author=self._author(entry),
            feed_title=feed_title,
            enclosure_url=enclosure_url,
            enclosure_type=enclosure_type,
            enclosure_length=enclosure_length,
            categories=self._categories(entry),
            updated_raw=updated_raw,
            fetched_at=fetched_at,
            raw={
                "title": self._title(entry),
                "link": link,
                "guid": self._id(entry),
                "published": published_raw,
                "updated": updated_raw,
                "enclosure": enclosure_url,
                "categories": self._categories(entry),
            },
        )
        if not item.title and not item.summary and not item.content:
            # No content at all: not a content item.
            return None
        return item

    @staticmethod
    def _title(entry) -> str:
        return _text(entry.find("title")) or _text(entry.find(f"{ATOM}title"))

    @staticmethod
    def _id(entry) -> str:
        return _text(entry.find("guid")) or _text(entry.find(f"{ATOM}id"))

    @staticmethod
    def _link(entry) -> str:
        # RSS: <link>text</link>
        direct = _text(entry.find("link"))
        if direct:
            return direct
        # Atom: <link rel="alternate" href="..."/> — prefer alternate, else the
        # first link that is not an enclosure.
        links = entry.findall(f"{ATOM}link")
        for candidate in links:
            if candidate.get("rel", "alternate") == "alternate" and candidate.get("href"):
                return candidate.get("href", "").strip()
        for candidate in links:
            if candidate.get("href"):
                return candidate.get("href", "").strip()
        return ""

    @staticmethod
    def _author(entry) -> str:
        # Atom namespaces everything, so ``<author>`` is
        # ``{http://www.w3.org/2005/Atom}author``; RSS 2.0 does not. Both have to
        # be looked for, otherwise every Atom feed silently loses its author.
        for tag in (f"{ATOM}author", "author"):
            author = entry.find(tag)
            if author is None:
                continue
            # Atom nests <name>; RSS <author> is usually a plain email.
            name = _text(author.find(f"{ATOM}name"))
            if name:
                return name
            text = _text(author)
            if text:
                return text
        return _text(entry.find(DC_CREATOR))

    @staticmethod
    def _times(entry) -> tuple[str, str]:
        published = (
            _text(entry.find("pubDate"))
            or _text(entry.find(f"{ATOM}published"))
            or _text(entry.find(DC_DATE))
        )
        updated = _text(entry.find(f"{ATOM}updated")) or _text(entry.find("lastBuildDate"))
        return published, updated

    @staticmethod
    def _categories(entry) -> list[str]:
        values = [_text(node) for node in entry.findall("category")]
        values += [node.get("term", "").strip() for node in entry.findall(f"{ATOM}category")]
        return [value for value in values if value]

    @staticmethod
    def _enclosure(entry) -> tuple[str, str, str]:
        node = entry.find("enclosure")
        if node is not None and node.get("url"):
            return (
                node.get("url", "").strip(),
                (node.get("type") or "").strip(),
                (node.get("length") or "").strip(),
            )
        for candidate in entry.findall(f"{ATOM}link"):
            if candidate.get("rel") == "enclosure" and candidate.get("href"):
                return (
                    candidate.get("href", "").strip(),
                    (candidate.get("type") or "").strip(),
                    (candidate.get("length") or "").strip(),
                )
        media = entry.find(MEDIA_CONTENT)
        if media is not None and media.get("url"):
            return (
                media.get("url", "").strip(),
                (media.get("type") or "").strip(),
                "",
            )
        return "", "", ""
