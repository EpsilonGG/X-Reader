"""Nitter timeline HTML parser.

Lineage:

* **X-rss** (`parsers/html_parser.py`) — the skeleton: walk ``div.timeline-item``,
  take the id from ``a.tweet-link``, the body from ``div.tweet-content``, the
  author from ``a.fullname`` / ``a.username``, and read the absolute date from
  the date anchor's ``title`` attribute. Kept.
* **x-tweet-fetcher** (`parsers/nitter_html.py`) — three behaviours adopted
  because X-rss silently loses this data: resolving Nitter's ``/pic/`` proxy
  URLs back to real twimg URLs, detecting retweets, and reading the engagement
  counters. Also adopted: keeping the *original* image URL from the anchor
  ``href`` rather than the downscaled ``img src``.

Additions over both:

* reply / quote / pinned / card extraction (all verified present in real Nitter
  markup), and
* an explicit "inside a quote block" guard, so a quoted tweet's media, stats or
  author can never leak into the outer tweet's record. X-rss's flat
  ``item.css(...)`` queries would happily pick those up.

Every selector used here was checked against a real captured Nitter page. Where
Nitter renders "no value" (an empty views counter) the field is ``None`` rather
than ``0``, because the two mean different things.
"""
from __future__ import annotations

import re

from selectolax.parser import HTMLParser
from selectolax.parser import Node

from domain.errors import ParsingError
from domain.models.account import Account
from domain.models.media import GIF
from domain.models.media import IMAGE
from domain.models.media import VIDEO
from domain.models.media import Media
from domain.models.tweet import RawTweet
from domain.models.tweet import Stats
from infrastructure.http.response import RawResponse
from parsers.base import BaseParser
from parsers.media_url import resolve_media_url
from parsers.timeutil import parse_source_date

# Re-exported: ``resolve_media_url`` is shared with the RSS parser and lives in
# ``parsers/media_url.py`` so both routes resolve Nitter proxy paths identically.
__all__ = ["NitterHtmlParser", "resolve_media_url", "parse_stat_number"]

_STAT_ICONS = {
    "icon-comment": "replies",
    "icon-retweet": "retweets",
    "icon-heart": "likes",
    "icon-views": "views",
}

_RETWEET_SUFFIX = re.compile(r"\s+retweeted\s*$", re.IGNORECASE)
_HANDLE = re.compile(r"@([A-Za-z0-9_]{1,15})")


def parse_stat_number(text: str) -> int | None:
    """Parse ``"3,819"`` -> ``3819``; empty or non-numeric -> ``None``."""
    cleaned = (text or "").strip().replace(",", "")
    if not cleaned or not cleaned.isdigit():
        return None
    return int(cleaned)


def _attr_or_text(node: Node | None, attribute: str = "title") -> str:
    """Nitter puts the clean value in an attribute and a display form in text."""
    if node is None:
        return ""
    value = (node.attributes.get(attribute) or "").strip()
    if value:
        return value
    return node.text(strip=True)


def _inside_quote(node: Node) -> bool:
    """Whether ``node`` sits inside a ``div.quote`` block.

    Guards every unscoped query: a quoted tweet's markup is nested inside the
    outer tweet, so without this the quote's author, media and stats would be
    attributed to the outer tweet.
    """
    current = node.parent
    depth = 0
    while current is not None and depth < 12:
        classes = current.attributes.get("class") or ""
        if "quote" in classes.split():
            return True
        current = current.parent
        depth += 1
    return False


def _is_gif(video: Node) -> bool:
    """Whether a ``<video>`` element is actually an animated GIF.

    Nitter marks these either on the element (``class="gif"``) or on the
    wrapping container (``div.attachments.media-gif``). Both are checked because
    which one carries the marker has changed between Nitter versions.
    """
    own = (video.attributes.get("class") or "").split()
    if "gif" in own:
        return True
    parent = video.parent
    depth = 0
    while parent is not None and depth < 4:
        classes = (parent.attributes.get("class") or "").split()
        if "media-gif" in classes or "gif" in classes:
            return True
        parent = parent.parent
        depth += 1
    return False


class NitterHtmlParser(BaseParser):
    """Parse a Nitter profile / search / status page into raw tweets."""

    name = "nitter_html"

    def parse(self, response: RawResponse, account: Account) -> list[RawTweet]:
        text = self.require_text(response, self.name)
        tree = HTMLParser(text)

        items = tree.css("div.timeline-item")
        if not items and tree.css_first("div.timeline") is None:
            # No tweet containers *and* no timeline container: this is not a
            # Nitter timeline page at all (an interstitial, a rate-limit notice,
            # a challenge page). Distinguishing this from "the timeline is
            # genuinely empty" turns an unhelpful "0 tweets" log line into a
            # diagnosable failure — the runner treats both the same way.
            head = " ".join(text.split())[:200]
            raise ParsingError(
                f"{self.name}: response from {response.url} is not a Nitter "
                f"timeline page (no div.timeline-item, no div.timeline); "
                f"body starts: {head!r}"
            )

        results: list[RawTweet] = []
        seen: set[str] = set()

        for item in items:
            record = self._parse_item(item, response, account)
            if record is None or record.tweet_id in seen:
                continue
            seen.add(record.tweet_id)
            results.append(record)

        return results

    # -- one tweet ----------------------------------------------------------
    def _parse_item(
        self, item: Node, response: RawResponse, account: Account
    ) -> RawTweet | None:
        link_node = item.css_first("a.tweet-link")
        href = (link_node.attributes.get("href") or "") if link_node else ""

        date_anchor = item.css_first("span.tweet-date a")
        if not href and date_anchor is not None:
            # Status pages render the focal tweet without a.tweet-link.
            href = date_anchor.attributes.get("href") or ""

        tweet_id = self.tweet_id_from_link(href)
        if not tweet_id:
            return None

        author_username = _attr_or_text(item.css_first("a.username")).lstrip("@")
        if not author_username:
            author_username = self.username_from_link(href)

        date_title = (date_anchor.attributes.get("title") or "") if date_anchor else ""
        date_text = date_anchor.text(strip=True) if date_anchor else ""
        created_at_raw = date_title or date_text

        content_node = item.css_first("div.tweet-content")
        content_text = content_node.text(separator="\n", strip=True) if content_node else ""

        retweet_text = ""
        retweet_node = item.css_first("div.retweet-header")
        if retweet_node is not None and not _inside_quote(retweet_node):
            retweet_text = retweet_node.text(strip=True)
        retweeted_by = _RETWEET_SUFFIX.sub("", retweet_text).strip() or None

        reply_to = self._parse_reply_to(item)
        quote = self._parse_quote(item)
        media, media_raw = self._parse_media(item)
        stats, stats_raw = self._parse_stats(item)
        card = self._parse_card(item)

        return RawTweet(
            tweet_id=tweet_id,
            account=account.username,
            url=self.canonical_url(author_username, tweet_id),
            text=content_text,
            created_at=parse_source_date(created_at_raw),
            created_at_raw=created_at_raw,
            author_username=author_username,
            author_name=_attr_or_text(item.css_first("a.fullname")) or author_username,
            is_retweet=retweeted_by is not None,
            retweeted_by=retweeted_by,
            is_reply=reply_to is not None,
            reply_to=reply_to,
            is_quote=quote is not None,
            quoted_tweet_id=quote["tweet_id"] if quote else None,
            quoted_author=quote["author"] if quote else None,
            quoted_text=quote["text"] if quote else None,
            is_pinned=item.css_first("div.pinned") is not None,
            media=media,
            stats=stats,
            provider="",
            route="",
            fetched_at=response.fetched_at.isoformat(),
            raw={
                "source": "nitter_html",
                "response_url": response.url,
                "link": href,
                "tweet_id": tweet_id,
                "author_username": author_username,
                "author_name": _attr_or_text(item.css_first("a.fullname")),
                "date_title": date_title,
                "date_text": date_text,
                "text": content_text,
                "is_pinned": item.css_first("div.pinned") is not None,
                "retweet_header": retweet_text,
                "replying_to": self._reply_text(item),
                "quote": quote,
                "media": media_raw,
                "stats": stats_raw,
                "card": card,
            },
        )

    # -- sub-extractors -----------------------------------------------------
    @staticmethod
    def _reply_text(item: Node) -> str:
        node = item.css_first("div.replying-to")
        if node is None or _inside_quote(node):
            return ""
        return node.text(strip=True)

    def _parse_reply_to(self, item: Node) -> str | None:
        """Nitter renders ``<div class="replying-to">Replying to <a>@user</a>``."""
        node = item.css_first("div.replying-to")
        if node is None or _inside_quote(node):
            return None

        anchor = node.css_first("a")
        if anchor is not None:
            handle = _attr_or_text(anchor).lstrip("@")
            if handle:
                return handle
            href = (anchor.attributes.get("href") or "").strip("/")
            if href:
                return href.split("/")[-1]

        match = _HANDLE.search(node.text(strip=True))
        return match.group(1) if match else None

    def _parse_quote(self, item: Node) -> dict | None:
        node = item.css_first("div.quote")
        if node is None:
            return None

        link_node = node.css_first("a.quote-link")
        href = (link_node.attributes.get("href") or "") if link_node else ""
        text_node = node.css_first("div.quote-text")

        return {
            "tweet_id": self.tweet_id_from_link(href) or None,
            "author": _attr_or_text(node.css_first("a.username")).lstrip("@") or None,
            "text": text_node.text(separator="\n", strip=True) if text_node else "",
            "link": href,
        }

    def _parse_media(self, item: Node) -> tuple[list[Media], list[dict]]:
        media: list[Media] = []
        raw: list[dict] = []
        seen: set[str] = set()

        for node in item.css("div.attachments a.still-image, div.attachments a.animated-gif"):
            if _inside_quote(node):
                continue
            source = node.attributes.get("href") or ""
            resolved = resolve_media_url(source)
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            kind = GIF if "animated-gif" in (node.attributes.get("class") or "") else IMAGE
            media.append(Media(url=resolved, type=kind))
            raw.append({"kind": kind, "source": source, "resolved": resolved})

        for video in item.css("div.attachments video"):
            if _inside_quote(video):
                continue
            source_node = video.css_first("source")
            source = (source_node.attributes.get("src") or "") if source_node else ""
            resolved = resolve_media_url(source)
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            # Nitter renders an animated GIF as a <video> inside
            # `div.attachments.media-gif` (with `class="gif"` on the element
            # itself). Treating those as VIDEO would silently mislabel every
            # GIF in the store, so the wrapper is checked explicitly.
            kind = GIF if _is_gif(video) else VIDEO
            poster = resolve_media_url(video.attributes.get("poster") or "")
            media.append(Media(url=resolved, type=kind, thumbnail=poster or None))
            raw.append(
                {"kind": kind, "source": source, "resolved": resolved, "poster": poster}
            )

        return media, raw

    def _parse_stats(self, item: Node) -> tuple[Stats, dict]:
        stats = Stats()
        raw: dict = {}

        container = None
        for candidate in item.css("div.tweet-stats"):
            if not _inside_quote(candidate):
                container = candidate
                break
        if container is None:
            return stats, raw

        for stat in container.css("span.tweet-stat"):
            field = None
            for icon_class, mapped in _STAT_ICONS.items():
                if stat.css_first(f"span.{icon_class}") is not None:
                    field = mapped
                    break
            if field is None:
                continue
            value = parse_stat_number(stat.text(strip=True))
            setattr(stats, field, value)
            raw[field] = value

        return stats, raw

    @staticmethod
    def _parse_card(item: Node) -> dict | None:
        """Nitter's link-preview card. Captured as evidence, not as a model field."""
        node = item.css_first("div.card")
        if node is None or _inside_quote(node):
            return None
        anchor = node.css_first("a.card-container")
        title = node.css_first("h2.card-title")
        description = node.css_first("p.card-description")
        destination = node.css_first("span.card-destination")
        return {
            "url": (anchor.attributes.get("href") or "") if anchor else "",
            "title": title.text(strip=True) if title else "",
            "description": description.text(strip=True) if description else "",
            "destination": destination.text(strip=True) if destination else "",
        }
