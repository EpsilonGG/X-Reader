"""Parser contract.

Boundary (architecture freeze, section 8): a parser understands bytes. It does
no network I/O, does not touch storage, does not know about scheduling, and
never emits RSS or summaries.

Signature note: X-rss's ``BaseParser.parse(response)`` derives the account from
the response URL. X-Reader passes the :class:`Account` explicitly instead, so a
tweet can be attributed to the tracked timeline even when the response URL does
not contain it (search routes, redirects, self-hosted instances with path
prefixes). That is a deliberate deviation, not an oversight.
"""
from __future__ import annotations

import re
from abc import ABC
from abc import abstractmethod

from domain.errors import ParsingError
from domain.models.account import Account
from domain.models.tweet import RawTweet
from infrastructure.http.response import RawResponse


class BaseParser(ABC):
    """Turns a raw response into raw tweet records."""

    #: Stable identifier, used for logging and the route→parser registry.
    name: str = "base"

    @abstractmethod
    def parse(self, response: RawResponse, account: Account) -> list[RawTweet]:
        """Parse ``response`` into tweets attributed to ``account``.

        Implementations must be tolerant: a malformed or unexpected document
        yields an empty list or a :class:`ParsingError`, never an unhandled
        exception. A page that simply contains no tweets is normal (a protected
        account, an empty timeline) and must not be treated as a failure.
        """
        raise NotImplementedError

    # -- shared helpers -----------------------------------------------------
    @staticmethod
    def canonical_url(author_username: str, tweet_id: str) -> str:
        """The canonical X permalink for a tweet.

        X-rss performs this rewrite in its post-processing stage
        (`processors/rss.py`), keyed off a regex on the channel title. Doing it
        at parse time removes that coupling and keeps the stored URL stable.
        """
        return f"https://x.com/{author_username}/status/{tweet_id}"

    @staticmethod
    def tweet_id_from_link(link: str) -> str:
        """Extract a tweet id from any Nitter/X permalink, ignoring fragments."""
        if not link:
            return ""
        path = link.split("#", 1)[0].split("?", 1)[0]
        match = re.search(r"/status/(\d+)", path)
        return match.group(1) if match else ""

    @staticmethod
    def username_from_link(link: str) -> str:
        """Extract the ``@handle`` (without @) that owns a permalink."""
        if not link:
            return ""
        path = link.split("#", 1)[0].split("?", 1)[0]
        match = re.search(r"/([A-Za-z0-9_]{1,15})/status/\d+", path)
        return match.group(1) if match else ""

    @staticmethod
    def require_text(response: RawResponse, parser_name: str) -> str:
        """Reject a response with no body — that is a parsing failure."""
        if response.is_empty:
            raise ParsingError(f"{parser_name}: empty response body from {response.url}")
        return response.text
