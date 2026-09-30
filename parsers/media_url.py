"""Nitter media-URL resolution, shared by both parsers.

Nitter never serves media directly. It proxies every image and video through
``/pic/<url-encoded real path>``, and image links carry an extra ``orig/``
segment for the full-size variant::

    /pic/orig/media%2FCNq2BQMWIAESvuO.jpg
        -> media/CNq2BQMWIAESvuO.jpg
        -> https://pbs.twimg.com/media/CNq2BQMWIAESvuO.jpg

    /pic/video.twimg.com%2Ftweet_video%2FHD-Yd7eXMAE3fsX.mp4
        -> video.twimg.com/tweet_video/HD-x.mp4
        -> https://video.twimg.com/tweet_video/HD-x.mp4

The rule is: after decoding, if the path already starts with a hostname it is
kept, otherwise it is a path on ``pbs.twimg.com``. The naive alternative
("always prefix pbs.twimg.com") silently produces ``pbs.twimg.com/video.twimg.com/...``
and breaks every video.

Why this is a shared module rather than a method on one parser: both the HTML
timeline route and the RSS route hand back the *same* Nitter proxy paths. X-rss
stores them unresolved, so every media URL in its output points at whichever
Nitter instance happened to answer — and dies with that instance. Resolving once,
in one place, keeps the two routes consistent and the stored URLs durable.
"""
from __future__ import annotations

import re
import urllib.parse

_PIC_PREFIX = re.compile(r"^orig/")


def resolve_media_url(href: str) -> str:
    """Turn a Nitter media path into the real twimg URL.

    Returns ``""`` for empty input. Absolute non-Nitter URLs are returned
    unchanged, so this is safe to call on an enclosure that is already direct.
    """
    if not href:
        return ""

    path = href.strip()

    # An absolute URL that is not a Nitter proxy is already the real thing.
    if path.startswith(("http://", "https://")) and "/pic/" not in path:
        return path

    # Keep only the proxy path, so both "/pic/x" and "https://host/pic/x" work.
    index = path.find("/pic/")
    if index != -1:
        path = path[index + len("/pic/"):]
    path = _PIC_PREFIX.sub("", path)

    decoded = urllib.parse.unquote(path)
    if decoded.startswith(("http://", "https://")):
        return decoded

    host, _, _rest = decoded.partition("/")
    if "." in host:
        return f"https://{decoded}"
    return f"https://pbs.twimg.com/{decoded}"
