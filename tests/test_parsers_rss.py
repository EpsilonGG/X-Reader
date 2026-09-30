"""RSS parser tests.

The RSS route is the narrower one: Nitter's feed carries no reply flag, no quote
block and no counters. These tests pin down both what it does extract and what
it deliberately leaves empty, so the honest limits stay documented rather than
being quietly "fixed" with guesses.
"""
from __future__ import annotations

import pytest

from domain.errors import ParsingError
from domain.models.media import IMAGE
from domain.models.media import VIDEO
from parsers.nitter_rss import NitterRssParser
from tests.conftest import fixture_text


@pytest.fixture
def parsed(feed_response, account):
    return NitterRssParser().parse(feed_response, account)


def by_id(tweets, tweet_id):
    for tweet in tweets:
        if tweet.tweet_id == tweet_id:
            return tweet
    raise AssertionError(f"tweet {tweet_id} not found in {[t.tweet_id for t in tweets]}")


# --- page-level -----------------------------------------------------------
def test_parses_items_that_have_a_permalink(parsed):
    # Five items; the one with no tweet permalink must be skipped.
    assert len(parsed) == 4
    assert "not-a-tweet-guid" not in {t.tweet_id for t in parsed}


def test_malformed_xml_raises_parsing_error(make_response, account):
    response = make_response(
        fixture_text("malformed_feed.xml"), content_type="application/rss+xml"
    )
    with pytest.raises(ParsingError) as excinfo:
        NitterRssParser().parse(response, account)
    assert "malformed XML" in str(excinfo.value)


def test_empty_body_raises_parsing_error(make_response, account):
    with pytest.raises(ParsingError):
        NitterRssParser().parse(make_response(""), account)


def test_channel_with_no_items_returns_empty(make_response, account):
    xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
    assert NitterRssParser().parse(make_response(xml), account) == []


# --- content --------------------------------------------------------------
def test_html_description_is_flattened_to_plain_text(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert tweet.text == "line one\nline two & more"


def test_author_name_comes_from_the_title_prefix(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert tweet.author_name == "jack"
    assert tweet.author_username == "jack"


def test_absolute_pubdate_parsed(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert tweet.created_at == "2024-09-11T19:31:00+00:00"
    assert tweet.created_at_raw == "Wed, 11 Sep 2024 19:31:00 GMT"


# --- retweets -------------------------------------------------------------
def test_retweet_recognised_and_author_taken_from_the_permalink(parsed):
    """X-rss reads the title's left half as the author, which is how it
    publishes authors literally named 'RT by @SCA_DI'."""
    tweet = by_id(parsed, "1000000000000000002")
    assert tweet.is_retweet is True
    assert tweet.retweeted_by == "SCA_DI"
    assert tweet.author_username == "SCA_DI"
    assert tweet.author_name == "SCA_DI"
    assert "RT by" not in tweet.author_name


def test_plain_item_is_not_a_retweet(parsed):
    assert by_id(parsed, "1000000000000000001").is_retweet is False


# --- media ----------------------------------------------------------------
def test_image_enclosure_resolved_off_the_nitter_proxy(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert len(tweet.media) == 1
    assert tweet.media[0].type == IMAGE
    assert tweet.media[0].url == "https://pbs.twimg.com/media/CNq2BQMWIAESvuO.jpg"


def test_video_enclosure_resolved_to_the_video_host(parsed):
    tweet = by_id(parsed, "1000000000000000003")
    assert len(tweet.media) == 1
    assert tweet.media[0].type == VIDEO
    assert tweet.media[0].url == "https://video.twimg.com/tweet_video/HD-clip.mp4"


def test_duplicate_enclosures_collapse_to_one(parsed):
    """`/pic/media%2FSAME.jpg` and `/pic/orig/media%2FSAME.jpg` are one asset."""
    tweet = by_id(parsed, "1000000000000000004")
    assert len(tweet.media) == 1
    assert tweet.media[0].url == "https://pbs.twimg.com/media/SAME.jpg"


# --- honest limits --------------------------------------------------------
def test_rss_route_leaves_relationships_and_counters_empty(parsed):
    """The feed simply does not carry this data; the HTML route does."""
    for tweet in parsed:
        assert tweet.is_reply is False
        assert tweet.reply_to is None
        assert tweet.is_quote is False
        assert tweet.quoted_tweet_id is None
        assert tweet.stats.is_empty()


def test_raw_keeps_the_original_item(parsed):
    tweet = by_id(parsed, "1000000000000000002")
    assert tweet.raw["source"] == "nitter_rss"
    assert tweet.raw["title"] == "RT by @SCA_DI: retweeted content"
    assert tweet.raw["link"].endswith("/status/1000000000000000002#m")


def test_parser_does_not_invent_provenance(parsed):
    assert all(t.provider == "" and t.route == "" for t in parsed)
