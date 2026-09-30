"""Validate the parsers against a *real captured* Nitter page.

Why this exists
---------------
``tests/`` uses self-authored fixtures. That keeps the suite hermetic, but it
cannot prove the fixtures resemble reality — if Nitter's markup changed, the
fixtures and the parser could be wrong together and every test would still
pass. This tool closes that gap: point it at a genuinely captured page and it
reports what the parsers actually extract from it.

It also cross-checks against x-tweet-fetcher's own parser when that project is
importable, which turns "I think my selectors are right" into a diff.

This is an operator/development tool. It is not part of the pipeline, and it is
deliberately *not* a pytest test: it depends on files outside this project, and
a test that silently skips is worse than a tool you choose to run.

Usage::

    # any real Nitter page you captured yourself
    python scripts/validate_against_real_page.py page.html

    # cross-check against the reference implementation, if present
    python scripts/validate_against_real_page.py page.html \\
        --xtf-src ../x-tweet-fetcher-main/src

Exit code: 0 if the page parsed into at least one record, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from datetime import timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.models.account import Account  # noqa: E402
from infrastructure.http.response import RawResponse  # noqa: E402
from parsers.nitter_html import NitterHtmlParser  # noqa: E402

#: Fields worth comparing one-to-one against the reference parser.
_COMPARABLE = (
    ("username", lambda o, t: o.get("username"), lambda t: t.author_username),
    ("replies", lambda o, t: o.get("replies"), lambda t: t.stats.replies),
    ("retweets", lambda o, t: o.get("retweets"), lambda t: t.stats.retweets),
    ("likes", lambda o, t: o.get("likes"), lambda t: t.stats.likes),
    ("views", lambda o, t: o.get("views"), lambda t: t.stats.views),
    ("has_media", lambda o, t: o.get("has_media"), lambda t: bool(t.media)),
)


def parse_page(path: Path, username: str):
    response = RawResponse(
        url=f"https://nitter.example/{username}",
        status_code=200,
        content_type="text/html",
        text=path.read_text(encoding="utf-8"),
        fetched_at=datetime.now(timezone.utc),
    )
    return NitterHtmlParser().parse(response, Account(username=username))


def describe(tweets) -> None:
    print(f"extracted {len(tweets)} records")
    print("-" * 74)
    for tweet in tweets:
        media = ",".join(m.type for m in tweet.media) or "-"
        print(f"  {tweet.tweet_id}  @{tweet.author_username}")
        print(f"    date    : {tweet.created_at_raw!r} -> {tweet.created_at}")
        print(f"    text    : {tweet.text[:64]!r}")
        print(
            f"    flags   : pinned={tweet.is_pinned} rt={tweet.is_retweet}"
            f"({tweet.retweeted_by}) reply={tweet.is_reply}({tweet.reply_to})"
            f" quote={tweet.is_quote}"
        )
        print(f"    media   : {media}")
        print(
            f"    stats   : replies={tweet.stats.replies} retweets={tweet.stats.retweets} "
            f"likes={tweet.stats.likes} views={tweet.stats.views}"
        )
        print()

    absent = {
        "created_at (relative date only)": sum(1 for t in tweets if t.created_at is None),
        "views (source rendered nothing)": sum(1 for t in tweets if t.stats.views is None),
        "text (media-only tweet)": sum(1 for t in tweets if not t.text),
    }
    print("records where a field is legitimately absent (None, not 0):")
    for label, count in absent.items():
        print(f"  {count:>4}  {label}")
    print()


def cross_check(tweets, html: str, xtf_src: Path) -> int:
    sys.path.insert(0, str(xtf_src))
    try:
        from xtf.parsers.nitter_html import _extract_tweets_from_events
        from xtf.parsers.nitter_html import _parse_html
    except Exception as exc:  # noqa: BLE001
        print(f"could not import the reference parser from {xtf_src}: {exc}")
        return 0

    reference = _extract_tweets_from_events(_parse_html(html).events, "https://nitter.example")
    theirs = {str(o.get("tweet_id") or ""): o for o in reference}
    mine = {t.tweet_id: t for t in tweets}

    print(f"reference parser : {len(theirs)} tweets")
    print(f"X-Reader         : {len(mine)} tweets")
    print(f"tweet id sets identical: {set(theirs) == set(mine)}")
    only_theirs = sorted(set(theirs) - set(mine))
    only_mine = sorted(set(mine) - set(theirs))
    if only_theirs:
        print(f"  only in reference : {only_theirs}")
    if only_mine:
        print(f"  only in X-Reader  : {only_mine}")
    print()

    differences = []
    compared = 0
    for tweet_id, tweet in mine.items():
        other = theirs.get(tweet_id)
        if other is None:
            continue
        compared += 1
        for name, get_ref, get_mine in _COMPARABLE:
            ref_value, my_value = get_ref(other, tweet), get_mine(tweet)
            if ref_value != my_value:
                differences.append(
                    f"  {tweet_id} {name}: reference={ref_value!r} X-Reader={my_value!r}"
                )

    print(f"compared {compared} tweets x {len(_COMPARABLE)} fields = "
          f"{compared * len(_COMPARABLE)} values")
    if differences:
        print(f"differences: {len(differences)}")
        for line in differences:
            print(line)
        print()
        print("NOTE: a difference is not automatically an X-Reader bug. The known")
        print("case is an empty counter span, which the reference parser coerces to")
        print("0 while X-Reader keeps None. Check the markup before 'fixing' either.")
    else:
        print("differences: none — full agreement")

    media_mismatch = sum(
        1
        for tweet_id, tweet in mine.items()
        if tweet_id in theirs
        and sorted(str(u) for u in (theirs[tweet_id].get("media_urls") or []))
        != sorted(m.url for m in tweet.media)
    )
    print(f"media URL sets differing: {media_mismatch}/{compared}")
    return len(differences)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("page", type=Path, help="a real captured Nitter HTML page")
    parser.add_argument("--username", default="jack", help="account the page belongs to")
    parser.add_argument(
        "--xtf-src",
        type=Path,
        default=None,
        help="path to x-tweet-fetcher's src/ directory, to cross-check against its parser",
    )
    args = parser.parse_args(argv)

    if not args.page.exists():
        print(f"no such file: {args.page}")
        return 1

    html = args.page.read_text(encoding="utf-8")
    print(f"page: {args.page}  ({len(html)} bytes)")
    print("=" * 74)
    tweets = parse_page(args.page, args.username)
    if not tweets:
        print("PARSED NOTHING — either the page is not a timeline, or the selectors")
        print("no longer match upstream markup.")
        return 1
    describe(tweets)

    if args.xtf_src:
        print("=" * 74)
        print("cross-check against the reference implementation")
        print("=" * 74)
        cross_check(tweets, html, args.xtf_src)

    return 0


if __name__ == "__main__":
    sys.exit(main())
