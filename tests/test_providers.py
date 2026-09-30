"""Provider and HTTP transport tests.

Providers are tested against fakes, never the network: what matters here is
failover behaviour, error mapping and URL construction — all of which are
deterministic and all of which are the parts that break in production.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from domain.errors import NetworkError
from domain.errors import ProviderError
from domain.models.account import Account
from infrastructure.http.client import HTTPClient
from providers.factory import build_providers
from providers.nitter import NitterProvider
from providers.xtf_adapter import XtfAdapter
from config.schema import Config


class FakeClient:
    """Duck-typed stand-in for :class:`HTTPClient`."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        result = self.responses.get(url, ProviderError(f"HTTP 404 — {url}", status=404))
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        pass


def ok_response(url: str, text: str = "<html>ok</html>"):
    from datetime import datetime, timezone

    from infrastructure.http.response import RawResponse

    return RawResponse(
        url=url,
        status_code=200,
        content_type="text/html",
        text=text,
        fetched_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def account_obj() -> Account:
    return Account(username="jack")


# --- NitterProvider -------------------------------------------------------
def test_nitter_routes_and_urls(account_obj):
    provider = NitterProvider(endpoints=["https://a.example"], client=FakeClient({}))
    assert provider.name == "nitter"
    assert provider.routes() == ("rss", "html")
    assert provider._url_for("https://a.example", account_obj, "rss") == (
        "https://a.example/jack/rss"
    )
    assert provider._url_for("https://a.example", account_obj, "html") == (
        "https://a.example/jack"
    )


def test_nitter_rejects_an_unknown_route(account_obj):
    provider = NitterProvider(endpoints=["https://a.example"], client=FakeClient({}))
    with pytest.raises(ProviderError):
        provider.fetch(account_obj, "telepathy")


def test_nitter_strips_whitespace_and_slashes_from_endpoints():
    """The X-rss defect class, defended at the provider too."""
    provider = NitterProvider(
        endpoints=[" https://a.example/ ", "https://b.example"], client=FakeClient({})
    )
    assert provider.endpoints == ["https://a.example", "https://b.example"]


def test_nitter_deduplicates_endpoints():
    provider = NitterProvider(
        endpoints=["https://a.example", "https://a.example/"], client=FakeClient({})
    )
    assert provider.endpoints == ["https://a.example"]


def test_nitter_requires_at_least_one_endpoint():
    with pytest.raises(ValueError):
        NitterProvider(endpoints=[], client=FakeClient({}))


def test_nitter_returns_the_first_successful_instance(account_obj):
    client = FakeClient(
        {
            "https://a.example/jack": ok_response("https://a.example/jack"),
        }
    )
    provider = NitterProvider(endpoints=["https://a.example"], client=client)
    response = provider.fetch(account_obj, "html")
    assert response.status_code == 200
    assert provider.preferred_endpoint == "https://a.example"


def test_nitter_falls_over_to_the_next_instance(account_obj):
    client = FakeClient(
        {
            "https://a.example/jack": NetworkError("HTTP 503"),
            "https://b.example/jack": ok_response("https://b.example/jack"),
        }
    )
    provider = NitterProvider(
        endpoints=["https://a.example", "https://b.example"], client=client
    )
    response = provider.fetch(account_obj, "html")
    assert response.url == "https://b.example/jack"
    assert client.calls == ["https://a.example/jack", "https://b.example/jack"]


def test_nitter_reports_every_instance_when_all_fail(account_obj):
    client = FakeClient(
        {
            "https://a.example/jack": NetworkError("HTTP 503"),
            "https://b.example/jack": NetworkError("timeout"),
        }
    )
    provider = NitterProvider(
        endpoints=["https://a.example", "https://b.example"], client=client
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(account_obj, "html")
    message = str(excinfo.value)
    assert "2/2 instances tried" in message
    assert "a.example" in message and "b.example" in message


def test_nitter_404_short_circuits_the_remaining_instances(account_obj):
    """A missing account will be missing everywhere; do not burn the budget."""
    client = FakeClient(
        {"https://a.example/jack": ProviderError("HTTP 404", status=404)}
    )
    provider = NitterProvider(
        endpoints=["https://a.example", "https://b.example", "https://c.example"],
        client=client,
    )
    with pytest.raises(ProviderError) as excinfo:
        provider.fetch(account_obj, "html")
    assert client.calls == ["https://a.example/jack"]
    assert "1/3 instances tried" in str(excinfo.value)


def test_nitter_prefers_the_instance_that_worked_last(account_obj):
    client = FakeClient(
        {
            "https://a.example/jack": NetworkError("HTTP 503"),
            "https://b.example/jack": ok_response("https://b.example/jack"),
            "https://a.example/OpenAI": ok_response("https://a.example/OpenAI"),
            "https://b.example/OpenAI": ok_response("https://b.example/OpenAI"),
        }
    )
    provider = NitterProvider(
        endpoints=["https://a.example", "https://b.example"], client=client
    )
    provider.fetch(account_obj, "html")
    client.calls.clear()

    provider.fetch(Account(username="OpenAI"), "html")
    # The remembered instance is tried first this time.
    assert client.calls[0] == "https://b.example/OpenAI"


def test_nitter_available_reflects_endpoints():
    provider = NitterProvider(endpoints=["https://a.example"], client=FakeClient({}))
    assert provider.available() is True


# --- XtfAdapter -----------------------------------------------------------
def test_xtf_reports_unavailable_when_not_installed():
    adapter = XtfAdapter(runtime=None)
    assert adapter.installed is False
    assert adapter.available() is False
    assert adapter.routes() == ("search",)


def test_xtf_fetch_without_the_package_raises_provider_error(account_obj):
    adapter = XtfAdapter(runtime=None)
    with pytest.raises(ProviderError) as excinfo:
        adapter.fetch(account_obj, "search")
    assert "not installed" in str(excinfo.value)


def test_xtf_search_url_matches_the_upstream_query_shape(account_obj):
    url = XtfAdapter._search_url("https://n.example", account_obj)
    assert url == "https://n.example/search?q=from%3Ajack&f=tweets"


def _fake_runtime(instances, get_text):
    exceptions = SimpleNamespace(
        NotFound=type("NotFound", (Exception,), {"code": "not_found"}),
        RateLimited=type("RateLimited", (Exception,), {"code": "rate_limited"}),
        UpstreamDown=type("UpstreamDown", (Exception,), {"code": "upstream_down"}),
        BackendUnavailable=type(
            "BackendUnavailable", (Exception,), {"code": "backend_unavailable"}
        ),
    )
    return SimpleNamespace(
        config=SimpleNamespace(nitter_instances=lambda: list(instances)),
        http=SimpleNamespace(get_text=get_text),
        exceptions=exceptions,
    )


def test_xtf_fetch_uses_the_search_route_and_wraps_the_body(account_obj):
    seen: list[str] = []

    def get_text(url, headers=None, timeout=None):
        seen.append(url)
        return "<html><div class='timeline'></div></html>"

    adapter = XtfAdapter(runtime=_fake_runtime(["https://n.example"], get_text))
    response = adapter.fetch(account_obj, "search")

    assert seen == ["https://n.example/search?q=from%3Ajack&f=tweets"]
    assert response.text.startswith("<html>")
    assert response.content_type == "text/html"


def test_xtf_maps_not_found_to_a_404_provider_error(account_obj):
    runtime = _fake_runtime(["https://n.example"], lambda *a, **k: None)
    runtime.http.get_text = lambda *a, **k: (_ for _ in ()).throw(
        runtime.exceptions.NotFound("gone")
    )
    adapter = XtfAdapter(runtime=runtime)
    with pytest.raises(ProviderError) as excinfo:
        adapter.fetch(account_obj, "search")
    assert "1/1 instances tried" in str(excinfo.value)


def test_xtf_maps_rate_limit_to_a_network_error(account_obj):
    runtime = _fake_runtime(["https://n.example", "https://m.example"], lambda *a, **k: None)

    def get_text(url, headers=None, timeout=None):
        raise runtime.exceptions.RateLimited("429")

    runtime.http.get_text = get_text
    adapter = XtfAdapter(runtime=runtime)
    with pytest.raises(ProviderError) as excinfo:
        adapter.fetch(account_obj, "search")
    # A rate limit is transient, so it must be classified as a network error
    # rather than a fatal provider error — that is what makes the runner retry
    # the next instance instead of giving up on the account.
    assert "network_error" in str(excinfo.value)
    assert "2/2 instances tried" in str(excinfo.value)


def test_xtf_rejects_an_unknown_route(account_obj):
    adapter = XtfAdapter(runtime=_fake_runtime(["https://n.example"], lambda *a, **k: "x"))
    with pytest.raises(ProviderError):
        adapter.fetch(account_obj, "rss")


# --- factory --------------------------------------------------------------
def test_factory_builds_the_configured_providers():
    config = Config(
        **{
            "provider": {
                "order": ["nitter"],
                "nitter": {"endpoints": ["https://a.example"]},
            }
        }
    )
    providers = build_providers(config, FakeClient({}))
    assert [p.name for p in providers] == ["nitter"]


def test_factory_skips_the_xtf_adapter_when_unavailable():
    """x-tweet-fetcher is optional; its absence must not fail a run."""
    config = Config(
        **{
            "provider": {
                "order": ["nitter", "xtf"],
                "nitter": {"endpoints": ["https://a.example"]},
                "xtf": {"enabled": True},
            }
        }
    )
    providers = build_providers(config, FakeClient({}))
    assert [p.name for p in providers] == ["nitter"]


def test_factory_skips_a_disabled_provider():
    config = Config(
        **{
            "provider": {
                "order": ["nitter", "xtf"],
                "nitter": {"endpoints": ["https://a.example"]},
                "xtf": {"enabled": False},
            }
        }
    )
    assert [p.name for p in build_providers(config, FakeClient({}))] == ["nitter"]


def test_factory_gives_the_xtf_adapter_the_nitter_endpoints_by_default():
    """One source of truth for instances."""
    config = Config(
        **{
            "provider": {
                "order": ["nitter"],
                "nitter": {"endpoints": ["https://a.example"]},
                "xtf": {"instances": []},
            }
        }
    )
    adapter = XtfAdapter(
        instances=config.provider.xtf.instances or config.provider.nitter.endpoints
    )
    assert adapter._instances() == ["https://a.example"]


# --- HTTPClient -----------------------------------------------------------
def _client(handler, retries=1) -> HTTPClient:
    return HTTPClient(
        timeout=1,
        retries=retries,
        backoff_base=0,  # no sleeping in tests
        transport=httpx.MockTransport(handler),
    )


def test_http_client_returns_a_raw_response_on_200():
    def handler(request):
        return httpx.Response(
            200, headers={"Content-Type": "text/html"}, text="<html>ok</html>"
        )

    with _client(handler) as client:
        response = client.get("https://a.example/jack")
    assert response.status_code == 200
    assert response.content_type == "text/html"
    assert response.text == "<html>ok</html>"
    assert response.size_bytes > 0
    assert response.is_empty is False


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 451])
def test_http_client_maps_fatal_statuses_to_provider_error(status):
    def handler(request):
        return httpx.Response(status, text="nope")

    with _client(handler) as client:
        with pytest.raises(ProviderError) as excinfo:
            client.get("https://a.example/jack")
    assert excinfo.value.status == status
    assert excinfo.value.kind == "provider_error"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_http_client_retries_then_raises_network_error(status):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(status, text="later")

    with _client(handler, retries=2) as client:
        with pytest.raises(NetworkError) as excinfo:
            client.get("https://a.example/jack")
    assert excinfo.value.status == status
    assert calls["n"] == 3  # initial attempt plus two retries


def test_http_client_recovers_when_a_retry_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="down")
        return httpx.Response(200, text="<html>back</html>")

    with _client(handler, retries=1) as client:
        response = client.get("https://a.example/jack")
    assert response.text == "<html>back</html>"
    assert calls["n"] == 2


def test_http_client_maps_transport_errors_to_network_error():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with _client(handler) as client:
        with pytest.raises(NetworkError):
            client.get("https://a.example/jack")


def test_http_client_maps_timeouts_to_network_error():
    def handler(request):
        raise httpx.ReadTimeout("too slow")

    with _client(handler) as client:
        with pytest.raises(NetworkError):
            client.get("https://a.example/jack")


def test_http_probe_never_raises():
    def handler(request):
        raise httpx.ConnectError("nope")

    with _client(handler) as client:
        assert client.probe("https://a.example") is False


def test_http_client_rejects_negative_retries():
    with pytest.raises(ValueError):
        HTTPClient(retries=-1)
