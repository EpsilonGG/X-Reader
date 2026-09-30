"""YouTube provider and factory tests.

The provider owns URL construction, channel lookup and error mapping — the three
things that break in production. All of it is deterministic, so none of it needs
the network: the client is a fake and the assertions are about the URL that would
have been requested.
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone

import pytest

from config.schema import Config
from config.schema import YoutubeChannelConfig
from domain.errors import NetworkError
from domain.errors import ProviderError
from domain.models.account import Account
from infrastructure.http.response import RawResponse
from providers.factory import build_youtube_provider
from providers.youtube import FEED_URL_TEMPLATE
from providers.youtube import ROUTE_FEED
from providers.youtube import YoutubeProvider
from providers.youtube import feed_url_for

from tests.conftest import YOUTUBE_CHANNEL_ID
from tests.conftest import YOUTUBE_OTHER_CHANNEL_ID


class FakeClient:
    """Duck-typed stand-in for :class:`HTTPClient`."""

    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    def get(self, url: str) -> RawResponse:
        self.calls.append(url)
        result = self.responses.get(url, NetworkError(f"no route to {url}"))
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        pass


def ok_response(url: str, text: str = "<feed/>") -> RawResponse:
    return RawResponse(
        url=url,
        status_code=200,
        content_type="application/atom+xml",
        text=text,
        fetched_at=datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc),
    )


def channel(channel_id: str = YOUTUBE_CHANNEL_ID, **overrides) -> YoutubeChannelConfig:
    data = {"channel_id": channel_id}
    data.update(overrides)
    return YoutubeChannelConfig(**data)


# --- URL construction -----------------------------------------------------
def test_the_public_feed_url_needs_no_api_key():
    url = feed_url_for(YOUTUBE_CHANNEL_ID)
    assert url == f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"
    # No key, no quota, no token — the whole reason this route was chosen.
    assert "key=" not in url
    assert "api" not in url


def test_url_for_generates_the_feed_url_from_the_channel_id():
    provider = YoutubeProvider([channel()], FakeClient())
    assert provider.url_for(YOUTUBE_CHANNEL_ID) == feed_url_for(YOUTUBE_CHANNEL_ID)


def test_an_explicit_url_overrides_the_generated_one():
    """The escape hatch for a mirror or a test double."""
    provider = YoutubeProvider([channel(url="https://mirror.example/feed.xml")], FakeClient())
    assert provider.url_for(YOUTUBE_CHANNEL_ID) == "https://mirror.example/feed.xml"


def test_url_for_an_unknown_channel_is_empty():
    provider = YoutubeProvider([channel()], FakeClient())
    assert provider.url_for("UCunknown") == ""


# --- shape ----------------------------------------------------------------
def test_name_and_routes():
    provider = YoutubeProvider([channel()], FakeClient())
    assert provider.name == "youtube"
    assert provider.routes() == (ROUTE_FEED,)
    assert ROUTE_FEED == "feed"


def test_channels_are_exposed_for_inspection():
    provider = YoutubeProvider([channel(), channel(YOUTUBE_OTHER_CHANNEL_ID)], FakeClient())
    assert {c.channel_id for c in provider.channels()} == {
        YOUTUBE_CHANNEL_ID,
        YOUTUBE_OTHER_CHANNEL_ID,
    }


# --- availability ---------------------------------------------------------
def test_available_with_one_enabled_channel():
    assert YoutubeProvider([channel()], FakeClient()).available() is True


def test_unavailable_when_every_channel_is_disabled():
    assert YoutubeProvider([channel(enabled=False)], FakeClient()).available() is False


def test_unavailable_when_no_channel_is_configured():
    assert YoutubeProvider([], FakeClient()).available() is False


# --- fetching -------------------------------------------------------------
def test_fetch_requests_the_channel_feed(youtube_channel):
    url = feed_url_for(YOUTUBE_CHANNEL_ID)
    client = FakeClient({url: ok_response(url)})
    provider = YoutubeProvider([channel()], client)

    response = provider.fetch(youtube_channel, ROUTE_FEED)

    assert client.calls == [url]
    assert response.status_code == 200


def test_each_channel_is_fetched_from_its_own_url():
    first = feed_url_for(YOUTUBE_CHANNEL_ID)
    second = feed_url_for(YOUTUBE_OTHER_CHANNEL_ID)
    client = FakeClient({first: ok_response(first), second: ok_response(second)})
    provider = YoutubeProvider(
        [channel(), channel(YOUTUBE_OTHER_CHANNEL_ID)], client
    )

    provider.fetch(Account(username=YOUTUBE_CHANNEL_ID), ROUTE_FEED)
    provider.fetch(Account(username=YOUTUBE_OTHER_CHANNEL_ID), ROUTE_FEED)

    assert client.calls == [first, second]


def test_one_channel_failing_does_not_stop_the_other():
    """Failures are per unit: the runner relies on this to isolate them."""
    first = feed_url_for(YOUTUBE_CHANNEL_ID)
    second = feed_url_for(YOUTUBE_OTHER_CHANNEL_ID)
    client = FakeClient(
        {first: NetworkError("boom"), second: ok_response(second)}
    )
    provider = YoutubeProvider([channel(), channel(YOUTUBE_OTHER_CHANNEL_ID)], client)

    with pytest.raises(NetworkError):
        provider.fetch(Account(username=YOUTUBE_CHANNEL_ID), ROUTE_FEED)
    assert provider.fetch(Account(username=YOUTUBE_OTHER_CHANNEL_ID), ROUTE_FEED)


def test_an_unknown_route_is_a_provider_error(youtube_channel):
    provider = YoutubeProvider([channel()], FakeClient())
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(youtube_channel, "timeline")
    assert "unknown route" in str(excinfo.value)


def test_an_unconfigured_channel_is_a_provider_error():
    provider = YoutubeProvider([channel()], FakeClient())
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(Account(username="UCnotconfigured"), ROUTE_FEED)
    assert "no configured channel" in str(excinfo.value)


# --- factory --------------------------------------------------------------
def test_factory_returns_none_when_no_channel_is_configured():
    config = Config(provider={"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}})
    assert build_youtube_provider(config, FakeClient()) is None


def test_factory_builds_a_provider_when_a_channel_is_enabled():
    config = Config(
        provider={"order": []},
        youtube_channels=[{"channel_id": YOUTUBE_CHANNEL_ID}],
    )
    provider = build_youtube_provider(config, FakeClient())
    assert isinstance(provider, YoutubeProvider)
    assert provider.name == "youtube"


def test_factory_ignores_disabled_channels():
    config = Config(
        provider={"order": []},
        rss_sources=[{"id": "site", "url": "https://e.example/rss.xml"}],
        youtube_channels=[{"channel_id": YOUTUBE_CHANNEL_ID, "enabled": False}],
    )
    assert build_youtube_provider(config, FakeClient()) is None


def test_the_template_constant_is_the_documented_one():
    assert FEED_URL_TEMPLATE == (
        "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    )
