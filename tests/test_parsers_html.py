"""HTML timeline parser tests.

These assert against ``fixtures/nitter_timeline.html``, which mirrors the DOM
that Nitter actually renders (selectors verified against a captured page). Each
test names the specific behaviour it protects, so a future change that breaks
one of them fails with a readable reason rather than a diff.
"""
from __future__ import annotations

import pytest

from domain.errors import ParsingError
from domain.models.media import GIF
from domain.models.media import IMAGE
from domain.models.media import VIDEO
from parsers.nitter_html import NitterHtmlParser
from tests.conftest import fixture_text


@pytest.fixture
def parsed(timeline_response, account):
    return NitterHtmlParser().parse(timeline_response, account)


def by_id(tweets, tweet_id):
    for tweet in tweets:
        if tweet.tweet_id == tweet_id:
            return tweet
    raise AssertionError(f"tweet {tweet_id} not found in {[t.tweet_id for t in tweets]}")


# --- page-level -----------------------------------------------------------
def test_parses_every_item_that_has_a_permalink(parsed):
    # Nine timeline items; the ninth has no permalink at all and must be dropped
    # rather than stored without an identity.
    assert len(parsed) == 8
    assert all(t.tweet_id for t in parsed)


def test_non_timeline_page_raises_parsing_error(make_response, account):
    """A rate-limit interstitial is a failure, not an empty timeline."""
    response = make_response(fixture_text("not_a_timeline.html"))
    with pytest.raises(ParsingError) as excinfo:
        NitterHtmlParser().parse(response, account)
    assert "not a Nitter" in str(excinfo.value)


def test_empty_timeline_is_not_an_error(make_response, account):
    """A correctly rendered page with no tweets means 'nothing to do'."""
    response = make_response(fixture_text("empty_timeline.html"))
    assert NitterHtmlParser().parse(response, account) == []


def test_empty_body_raises_parsing_error(make_response, account):
    response = make_response("   ")
    with pytest.raises(ParsingError):
        NitterHtmlParser().parse(response, account)


def test_duplicate_ids_on_one_page_are_collapsed(make_response, account):
    html = """
    <div class="timeline">
      <div class="timeline-item">
        <a class="tweet-link" href="/jack/status/555#m"></a>
        <div class="tweet-content">first copy</div>
      </div>
      <div class="timeline-item">
        <a class="tweet-link" href="/jack/status/555#m"></a>
        <div class="tweet-content">second copy</div>
      </div>
    </div>
    """
    tweets = NitterHtmlParser().parse(make_response(html), account)
    assert [t.tweet_id for t in tweets] == ["555"]


# --- identity and content -------------------------------------------------
def test_pinned_flag_and_absolute_date(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert tweet.is_pinned is True
    assert tweet.created_at == "2024-09-11T19:31:00+00:00"
    assert tweet.created_at_raw == "Sep 11, 2024 · 7:31 PM UTC"


def test_text_and_author_and_url(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert tweet.text == "just setting up my twttr & nothing else"
    assert tweet.author_username == "jack"
    assert tweet.author_name == "jack"
    assert tweet.url == "https://x.com/jack/status/1000000000000000001"
    assert tweet.account == "jack"


def test_relative_date_is_left_unresolved(parsed):
    """A relative stamp has no absolute meaning, so guessing is refused."""
    tweet = by_id(parsed, "1000000000000000008")
    assert tweet.created_at_raw == "3h"
    assert tweet.created_at is None


# --- relationships --------------------------------------------------------
def test_retweet_detected_and_header_text_stripped(parsed):
    tweet = by_id(parsed, "1000000000000000002")
    assert tweet.is_retweet is True
    assert tweet.retweeted_by == "jack"
    assert tweet.author_username == "OpenAI"


def test_plain_tweet_is_not_a_retweet(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert tweet.is_retweet is False
    assert tweet.retweeted_by is None


def test_reply_target_detected(parsed):
    tweet = by_id(parsed, "1000000000000000003")
    assert tweet.is_reply is True
    assert tweet.reply_to == "elonmusk"


def test_quote_captured(parsed):
    tweet = by_id(parsed, "1000000000000000004")
    assert tweet.is_quote is True
    assert tweet.quoted_tweet_id == "1000000000000000099"
    assert tweet.quoted_author == "someoneelse"
    assert tweet.quoted_text == "The quoted body text."


def test_non_quote_tweet_has_no_quote_fields(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert tweet.is_quote is False
    assert tweet.quoted_tweet_id is None


# --- the quote-leak guard (X-rss's flat queries fail this) ----------------
def test_quoted_media_does_not_leak_into_the_outer_tweet(parsed):
    tweet = by_id(parsed, "1000000000000000004")
    urls = [m.url for m in tweet.media]
    assert urls == [], f"quoted media leaked into the outer tweet: {urls}"


def test_quoted_stats_do_not_leak_into_the_outer_tweet(parsed):
    tweet = by_id(parsed, "1000000000000000004")
    # The quote's block says 999 likes; the outer tweet's says 42.
    assert tweet.stats.likes == 42
    assert tweet.stats.replies == 7


def test_quoted_author_does_not_replace_the_outer_author(parsed):
    tweet = by_id(parsed, "1000000000000000004")
    assert tweet.author_username == "AnthropicAI"


# --- stats ----------------------------------------------------------------
def test_counters_parsed_with_thousands_separators(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert (tweet.stats.replies, tweet.stats.retweets) == (1234, 5678)
    assert (tweet.stats.likes, tweet.stats.views) == (9012, 345678)


def test_empty_counter_is_none_not_zero(parsed):
    """Nitter renders an empty views span; that is 'unknown', not 'zero'."""
    tweet = by_id(parsed, "1000000000000000004")
    assert tweet.stats.views is None


def test_missing_stats_block_yields_all_none(parsed):
    tweet = by_id(parsed, "1000000000000000002")
    assert tweet.stats.is_empty()


# --- media ----------------------------------------------------------------
def test_image_resolved_to_original_full_size(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    assert len(tweet.media) == 1
    media = tweet.media[0]
    assert media.type == IMAGE
    assert media.url == "https://pbs.twimg.com/media/CNq2BQMWIAESvuO.jpg"


def test_video_resolved_to_video_host_with_poster_thumbnail(parsed):
    tweet = by_id(parsed, "1000000000000000005")
    assert len(tweet.media) == 1
    media = tweet.media[0]
    assert media.type == VIDEO
    assert media.url == "https://video.twimg.com/tweet_video/HD-Yd7eXMAE3fsX.mp4"
    assert media.thumbnail == (
        "https://pbs.twimg.com/amplify_video_thumb/THUMBNAIL.jpg"
    )


def test_gif_inside_media_gif_wrapper_is_a_gif(parsed):
    tweet = by_id(parsed, "1000000000000000006")
    assert len(tweet.media) == 1
    assert tweet.media[0].type == GIF
    assert tweet.media[0].url == "https://video.twimg.com/tweet_video/HD-gifclip.mp4"


# --- card and raw evidence ------------------------------------------------
def test_link_card_is_kept_in_raw_but_not_modelled(parsed):
    tweet = by_id(parsed, "1000000000000000007")
    assert tweet.raw["card"]["title"] == "A blog post"
    assert tweet.raw["card"]["destination"] == "example.com"
    # Not a model field: the freeze warns against speculative fields.
    assert not hasattr(tweet, "card")


def test_raw_keeps_the_source_evidence(parsed):
    tweet = by_id(parsed, "1000000000000000001")
    raw = tweet.raw
    assert raw["source"] == "nitter_html"
    assert raw["link"] == "/jack/status/1000000000000000001#m"
    assert raw["date_title"] == "Sep 11, 2024 · 7:31 PM UTC"
    assert raw["is_pinned"] is True
    assert raw["media"][0]["source"] == "/pic/orig/media%2FCNq2BQMWIAESvuO.jpg"
    assert raw["stats"]["likes"] == 9012


def test_raw_keeps_the_retweet_header_verbatim(parsed):
    tweet = by_id(parsed, "1000000000000000002")
    assert tweet.raw["retweet_header"] == "jack retweeted"


# --- boundary -------------------------------------------------------------
def test_parser_does_not_invent_provenance(parsed):
    """Provenance is the runner's job: one parser serves two providers."""
    assert all(t.provider == "" and t.route == "" for t in parsed)


def test_parser_stamps_fetch_time(parsed):
    assert all(t.fetched_at == "2024-09-11T19:31:00+00:00" for t in parsed)
