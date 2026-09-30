"""Nitter RSS feed parser.

Lineage: **X-rss** (`parsers/rss_parser.py`) — ``ElementTree`` over
``./channel/item``, reading ``link`` / ``title`` / ``description`` / ``pubDate``
/ ``enclosure``, with ``<br>`` flattened to newlines and tags stripped. Kept,
because that parser runs in production today.

Two deliberate improvements:

* **The author handle comes from the item's ``<link>``, not from the title.**
  X-rss splits the title on ``":"`` and treats the left half as the username,
  which is why its generated feeds contain author names like
  ``RT by @SCA_DI``. The permalink path already carries the real handle.
* **Retweets are recognised explicitly** from Nitter's ``RT by @handle:`` title
  prefix, instead of being swallowed into the author name.

Honest limits, not silently papered over: a Nitter RSS item carries no reply
flag, no quote block and no engagement counters, so those stay ``None``/``False``
here. The HTML route is what fills them in — which is exactly why the provider
declares both routes.
"""
from __future__ import annotations

import re
from html import unescape
from xml.etree import ElementTree

from domain.errors import ParsingError
from domain.models.account import Account
from domain.models.media import IMAGE
from domain.models.media import VIDEO
from domain.models.media import Media
from domain.models.tweet import RawTweet
from infrastructure.http.response import RawResponse
from parsers.base import BaseParser
from parsers.media_url import resolve_media_url
from parsers.timeutil import parse_source_date

_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_RETWEET_TITLE = re.compile(r"^RT by @([A-Za-z0-9_]{1,15})\s*:\s*", re.IGNORECASE)


def _localname(tag: str) -> str:
    """Strip an XML namespace: ``{uri}creator`` -> ``creator``."""
    return tag.rsplit("}", 1)[-1].lower()


def _find_text(item: ElementTree.Element, *names: str) -> str:
    """First non-empty child matching any of ``names`` (namespace-agnostic)."""
    wanted = {n.lower() for n in names}
    for child in item:
        if _localname(child.tag) in wanted:
            text = (child.text or "").strip()
            if text:
                return text
    return ""


def clean_html(value: str) -> str:
    """Flatten Nitter's HTML ``<description>`` into readable plain text."""
    if not value:
        return ""
    text = _BR.sub("\n", value)
    text = _TAG.sub("", text)
    return unescape(text).strip()


class NitterRssParser(BaseParser):
    """Parse a Nitter RSS feed into raw tweets."""

    name = "nitter_rss"

    def parse(self, response: RawResponse, account: Account) -> list[RawTweet]:
        text = self.require_text(response, self.name)
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise ParsingError(
                f"{self.name}: malformed XML from {response.url}: {exc}"
            ) from exc

        results: list[RawTweet] = []
        seen: set[str] = set()

        for item in root.iter():
            if _localname(item.tag) != "item":
                continue
            record = self._parse_item(item, response, account)
            if record is None or record.tweet_id in seen:
                continue
            seen.add(record.tweet_id)
            results.append(record)

        return results

    # -- one item -----------------------------------------------------------
    def _parse_item(
        self, item: ElementTree.Element, response: RawResponse, account: Account
    ) -> RawTweet | None:
        link = _find_text(item, "link")
        guid = _find_text(item, "guid")
        title = _find_text(item, "title")
        description = _find_text(item, "description")
        pub_date = _find_text(item, "pubDate", "date")

        tweet_id = self.tweet_id_from_link(link) or self.tweet_id_from_link(guid)
        if not tweet_id:
            return None

        author_username = self.username_from_link(link) or self.username_from_link(guid)

        retweeted_by = None
        title_text = title
        match = _RETWEET_TITLE.match(title)
        if match:
            retweeted_by = match.group(1)
            title_text = title[match.end():].strip()
        elif author_username:
            # Plain titles look like "Display Name: text".
            prefix, sep, rest = title.partition(": ")
            if sep and rest:
                title_text = rest.strip()

        content = clean_html(description) or title_text
        author_name = ""
        if not retweeted_by:
            prefix, sep, _rest = title.partition(": ")
            if sep:
                author_name = prefix.strip()

        media, media_raw = self._parse_enclosures(item)

        return RawTweet(
            tweet_id=tweet_id,
            account=account.username,
            url=self.canonical_url(author_username, tweet_id),
            text=content,
            created_at=parse_source_date(pub_date),
            created_at_raw=pub_date,
            author_username=author_username,
            author_name=author_name or author_username,
            is_retweet=retweeted_by is not None,
            retweeted_by=retweeted_by,
            media=media,
            fetched_at=response.fetched_at.isoformat(),
            raw={
                "source": "nitter_rss",
                "response_url": response.url,
                "title": title,
                "description": description,
                "pubDate": pub_date,
                "link": link,
                "guid": guid,
                "media": media_raw,
                "creator": _find_text(item, "creator", "author"),
            },
        )

    @staticmethod
    def _parse_enclosures(item: ElementTree.Element) -> tuple[list[Media], list[dict]]:
        media: list[Media] = []
        raw: list[dict] = []
        seen: set[str] = set()

        for child in item:
            if _localname(child.tag) != "enclosure":
                continue
            url = (child.attrib.get("url") or "").strip()
            content_type = (child.attrib.get("type") or "").strip()
            if not url:
                continue
            # Nitter's RSS enclosures point at the instance's own /pic/ proxy,
            # exactly like the HTML route does. Resolving them keeps stored
            # media URLs valid after the instance that served them goes away —
            # and keeps the two routes' output identical in shape.
            resolved = resolve_media_url(url)
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            kind = VIDEO if content_type.startswith("video/") else IMAGE
            media.append(Media(url=resolved, type=kind))
            raw.append(
                {"kind": kind, "source": url, "resolved": resolved, "type": content_type}
            )

        return media, raw
