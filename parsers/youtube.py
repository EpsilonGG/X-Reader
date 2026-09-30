"""YouTube channel feed parser (Atom, no API key).

This is the *raw* side of the YouTube source family. It understands one thing:
the Atom document that ``https://www.youtube.com/feeds/videos.xml?channel_id=...``
returns. It is deliberately **not** a general Atom parser and **not** the RSS
parser:

* the RSS parser is generic by design (any feed, any site) and its identity
  rules are URL-reduction based, because a feed's ``<guid>`` is usually just its
  link;
* YouTube's feed carries a real, stable, globally unique identifier
  (``<yt:videoId>``), so its identity rule is simply "that id", and its payload
  lives in the ``media:`` namespace rather than in ``<description>``.

Sharing one parser between them would mean one of the two had to carry rules the
other does not need — exactly the "both are XML, so reuse the parser" shortcut
the architecture forbids. The two *do* share one pure helper,
:func:`parsers.rss.parse_feed_time`, because feed time strings are genuinely the
same problem and a second, divergent implementation is how a nine-hour timezone
bug gets reintroduced (Decision Record DR-13).

Boundary (freeze section 8): a parser understands bytes. No network, no storage,
no dedup, no delivery, no Telegram/QQ, no summary, no HTML rendering. It returns
:class:`RawYouTubeItem` records and stops.

Handled shapes
--------------
* feed level — ``<yt:channelId>``, ``<title>``, ``<author><name>``. The channel
  is taken from the **feed**, because that is where the feed actually states it;
  an entry repeats it, and the entry value wins when present.
* entry — ``<yt:videoId>`` (identity), ``<title>``, ``<published>``,
  ``<updated>`` (evidence only), ``<link rel="alternate">``,
  ``<media:group>`` with ``media:title``, ``media:description``,
  ``media:thumbnail@url``, ``media:content@duration`` and
  ``media:community/media:statistics@views``.

Time handling (freeze v3 section 7, DR-13)
------------------------------------------
YouTube states ``<published>`` as ISO-8601 **with** an offset
(``2026-09-28T11:45:00+00:00``), so it is normalised to UTC and reported at
second precision. Nothing is invented: if the value is unparseable,
``published_at`` stays empty with precision ``unknown`` and the source's own
string is kept in ``published_raw``.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree

from domain.errors import ParsingError
from domain.models.account import Account
from domain.models.youtube_item import RawYouTubeItem
from parsers.base import BaseParser
from parsers.rss import parse_feed_time

ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"

MEDIA_GROUP = f"{MEDIA}group"
MEDIA_DESCRIPTION = f"{MEDIA}description"
MEDIA_THUMBNAIL = f"{MEDIA}thumbnail"
MEDIA_CONTENT = f"{MEDIA}content"
MEDIA_TITLE = f"{MEDIA}title"
MEDIA_STATISTICS = f"{MEDIA}statistics"

#: ``<id>`` is ``yt:video:<video_id>`` at entry level and ``yt:channel:<id>`` at
#: feed level. Used only as a fallback: ``<yt:videoId>`` is stated directly.
_VIDEO_PREFIX = "yt:video:"
_CHANNEL_PREFIX = "yt:channel:"

#: A YouTube video id is 11 characters of ``[A-Za-z0-9_-]``. Not enforced as a
#: hard filter — a future format change must not silently drop every item — but
#: used to sanity-check the ``<id>`` fallback.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _text(element) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


class YoutubeParser(BaseParser):
    """Turns a YouTube channel feed into :class:`RawYouTubeItem` records."""

    name = "youtube"

    def parse(self, response, account: Account) -> list[RawYouTubeItem]:
        """Parse ``response`` into videos attributed to ``account``.

        ``account`` carries the tracked unit's identifier, which for a YouTube
        channel is its ``channel_id``. The Provider/Parser interface was frozen
        in Phase 1 and is deliberately not changed here.
        """
        body = self.require_text(response, self.name)
        fetched_at = response.fetched_at.isoformat()

        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ParsingError(
                f"youtube: not well-formed XML from {response.url}: {exc}"
            ) from exc

        if root.tag not in ("feed", f"{ATOM}feed"):
            raise ParsingError(
                f"youtube: {response.url} is XML but not a YouTube channel feed "
                f"(root <{root.tag}>)"
            )

        # Feed-level channel identity. The tracked unit's identifier is used as
        # the last fallback so an entry is never attributed to nothing.
        feed_channel_id = (
            _text(root.find(f"{YT}channelId"))
            or self._channel_id_from_feed_id(_text(root.find(f"{ATOM}id")))
            or (account.username or "").strip()
        )
        feed_channel_name = _text(root.find(f"{ATOM}title")) or _text(
            root.find(f"{ATOM}author/{ATOM}name")
        )

        videos: list[RawYouTubeItem] = []
        for entry in root.findall(f"{ATOM}entry"):
            try:
                item = self._entry(
                    entry,
                    feed_channel_id=feed_channel_id,
                    feed_channel_name=feed_channel_name,
                    fetched_at=fetched_at,
                )
            except Exception:  # noqa: BLE001 - one bad entry must not kill the feed
                continue
            if item is not None:
                videos.append(item)
        return videos

    # -- feed level ---------------------------------------------------------
    @staticmethod
    def _channel_id_from_feed_id(feed_id: str) -> str:
        if feed_id.startswith(_CHANNEL_PREFIX):
            return feed_id[len(_CHANNEL_PREFIX):].strip()
        return ""

    # -- one entry ----------------------------------------------------------
    def _entry(
        self,
        entry,
        *,
        feed_channel_id: str,
        feed_channel_name: str,
        fetched_at: str,
    ) -> RawYouTubeItem | None:
        video_id = self._video_id(entry)
        if not video_id:
            # No identity means the record cannot be deduplicated or addressed
            # for delivery. Dropping it here keeps the loss visible in the
            # fetched/normalized counts rather than storing an unusable row.
            return None

        group = entry.find(MEDIA_GROUP)
        title = _text(entry.find(f"{ATOM}title")) or _text(
            group.find(MEDIA_TITLE) if group is not None else None
        )
        description = _text(group.find(MEDIA_DESCRIPTION) if group is not None else None)

        if not title and not description:
            # Nothing to say about it. Same rule as the RSS parser: a record with
            # no content at all is not a content item.
            return None

        published_raw = _text(entry.find(f"{ATOM}published"))
        published_at, precision = parse_feed_time(published_raw)

        channel_id = _text(entry.find(f"{YT}channelId")) or feed_channel_id
        channel = (
            _text(entry.find(f"{ATOM}author/{ATOM}name"))
            or feed_channel_name
        )

        return RawYouTubeItem(
            video_id=video_id,
            title=title,
            description=description,
            channel=channel,
            channel_id=channel_id,
            published_raw=published_raw,
            published_at=published_at,
            published_precision=precision,
            thumbnail_url=self._thumbnail(group),
            url=self._link(entry),
            duration=self._duration(group),
            view_count=self._view_count(group),
            fetched_at=fetched_at,
            raw={
                "video_id": video_id,
                "title": title,
                "description": description,
                "channel": channel,
                "channel_id": channel_id,
                "published": published_raw,
                "updated": _text(entry.find(f"{ATOM}updated")),
                "link": self._link(entry),
                "thumbnail": self._thumbnail(group),
                "duration": self._duration(group),
                "views": self._view_count(group),
            },
        )

    @staticmethod
    def _video_id(entry) -> str:
        """``<yt:videoId>`` first, then the ``yt:video:<id>`` form of ``<id>``."""
        direct = _text(entry.find(f"{YT}videoId"))
        if direct:
            return direct
        raw_id = _text(entry.find(f"{ATOM}id"))
        if raw_id.startswith(_VIDEO_PREFIX):
            candidate = raw_id[len(_VIDEO_PREFIX):].strip()
            if candidate:
                return candidate
        # Last resort: some mirrors put a bare id in <id>. Accepted only when it
        # looks like a video id, so a channel-level id can never be mistaken for
        # one and give every entry the same identity.
        if _VIDEO_ID_RE.match(raw_id):
            return raw_id
        return ""

    @staticmethod
    def _link(entry) -> str:
        for candidate in entry.findall(f"{ATOM}link"):
            if candidate.get("rel", "alternate") == "alternate" and candidate.get("href"):
                return candidate.get("href", "").strip()
        for candidate in entry.findall(f"{ATOM}link"):
            if candidate.get("href"):
                return candidate.get("href", "").strip()
        return ""

    @staticmethod
    def _thumbnail(group) -> str:
        if group is None:
            return ""
        node = group.find(MEDIA_THUMBNAIL)
        if node is None:
            return ""
        return (node.get("url") or "").strip()

    @staticmethod
    def _duration(group) -> str:
        """``media:content@duration`` verbatim — YouTube states whole seconds."""
        if group is None:
            return ""
        node = group.find(MEDIA_CONTENT)
        if node is None:
            return ""
        return (node.get("duration") or "").strip()

    @staticmethod
    def _view_count(group) -> int | None:
        if group is None:
            return None
        node = group.find(f"{MEDIA}community/{MEDIA_STATISTICS}")
        if node is None or node.get("views") is None:
            return None
        try:
            return int(node.get("views", ""))
        except (TypeError, ValueError):
            return None
