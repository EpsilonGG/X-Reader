"""Configuration tests.

Two of these are regression tests for defects found in X-rss during the
analysis phase:

* six of the fourteen endpoints in its live ``config.yaml`` carry a trailing
  space, and its fetcher only strips ``/`` — so those endpoints silently never
  work. X-Reader strips whitespace during validation instead.
* unknown provider names and empty endpoint lists fail loudly at load time
  rather than surfacing as an all-accounts failure at run time.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_accounts
from config.loader import load_config
from config.schema import Config
from domain.errors import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


MINIMAL = """
provider:
  order: [nitter]
  nitter:
    endpoints:
      - https://a.example
    routes: [rss, html]
"""


# --- endpoints ------------------------------------------------------------
def test_endpoint_trailing_whitespace_is_stripped(tmp_path):
    """Defensive hardening, not a regression fix.

    X-rss's shipped ``config.yaml`` *looks* like it has six endpoints with a
    trailing space, but they are unquoted YAML plain scalars and YAML strips
    that whitespace before Python ever sees it — loading X-rss's file yields 14
    clean endpoints. Whitespace can still arrive for real through a **quoted**
    scalar (as below) or a hand-edited/`--accounts`-style override, so the
    validator strips it. That is what this test pins down.
    """
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  nitter:
    endpoints:
      - "https://nitter.privacyredirect.com "
      - "  https://nuku.trabun.org"
      - "https://nitter.meowing.monster  "
""",
    )
    config = load_config(path)
    assert config.provider.nitter.endpoints == [
        "https://nitter.privacyredirect.com",
        "https://nuku.trabun.org",
        "https://nitter.meowing.monster",
    ]


def test_endpoint_trailing_slash_is_stripped(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        "provider:\n  nitter:\n    endpoints:\n      - https://a.example/\n",
    )
    assert load_config(path).provider.nitter.endpoints == ["https://a.example"]


def test_duplicate_endpoints_are_collapsed(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  nitter:
    endpoints:
      - https://a.example
      - https://a.example/
      - https://a.example
""",
    )
    assert load_config(path).provider.nitter.endpoints == ["https://a.example"]


def test_blank_endpoints_are_dropped(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        'provider:\n  nitter:\n    endpoints:\n      - "  "\n      - https://a.example\n',
    )
    assert load_config(path).provider.nitter.endpoints == ["https://a.example"]


# --- validation -----------------------------------------------------------
def test_empty_endpoint_list_is_rejected(tmp_path):
    path = write(tmp_path, "config.yaml", "provider:\n  nitter:\n    endpoints: []\n")
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_unknown_provider_is_rejected(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: [nitter, mystery]
  nitter:
    endpoints: [https://a.example]
""",
    )
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_empty_provider_order_is_rejected_when_there_is_no_other_input(tmp_path):
    """``order: []`` disables X — that is legal. Having *nothing* is not.

    This test used to be ``test_empty_provider_order_is_rejected``, and it held
    because Phase 1 assumed X was the only possible input. Phase 2 made RSS an
    independent input source, so the contract it encodes has changed: the
    rejection is now about "no input source at all", not about X specifically.
    ``provider.order: []`` *plus* an enabled feed is accepted — see
    ``test_rss_only_config_is_accepted``.
    """
    path = write(tmp_path, "config.yaml", "provider:\n  order: []\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "no input source" in str(excinfo.value)


# --- input-source matrix --------------------------------------------------
# X and RSS are independent inputs. What matters is that there is at least one
# of them; "no X" is a configuration choice, "nothing at all" is an error.
def test_x_only_config_is_accepted(tmp_path):
    path = write(tmp_path, "config.yaml", MINIMAL)
    config = load_config(path)
    assert config.x_input_enabled is True
    assert config.enabled_rss_sources == []


def test_rss_only_config_is_accepted(tmp_path):
    """``provider.order: []`` + one enabled feed — the Phase 2 RSS-only mode."""
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: []
rss_sources:
  - id: visualnovel
    url: "https://example.com/interview.xml"
    enabled: true
""",
    )
    config = load_config(path)
    assert config.provider.order == []
    assert config.x_input_enabled is False
    assert [s.id for s in config.enabled_rss_sources] == ["visualnovel"]
    # Crucially, no Nitter endpoints were required.
    assert config.provider.nitter.endpoints == []


def test_x_and_rss_config_is_accepted(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL
        + """
rss_sources:
  - id: visualnovel
    url: "https://example.com/interview.xml"
""",
    )
    config = load_config(path)
    assert config.x_input_enabled is True
    assert [s.id for s in config.enabled_rss_sources] == ["visualnovel"]


def test_no_input_source_at_all_is_rejected(tmp_path):
    """X disabled *and* no feed is the one combination that must still fail."""
    path = write(tmp_path, "config.yaml", "provider:\n  order: []\nrss_sources: []\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "no input source" in str(excinfo.value)


def test_x_disabled_with_only_a_disabled_feed_is_rejected(tmp_path):
    """A disabled source is not an input source."""
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: []
rss_sources:
  - id: visualnovel
    url: "https://example.com/interview.xml"
    enabled: false
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "no input source" in str(excinfo.value)


def test_x_enabled_still_requires_valid_nitter_config(tmp_path):
    """Allowing RSS-only must not weaken X validation."""
    # nitter in the order but no endpoints.
    path = write(tmp_path, "config.yaml", "provider:\n  order: [nitter]\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "nitter.endpoints" in str(excinfo.value)


def test_x_enabled_with_a_feed_still_requires_nitter_endpoints(tmp_path):
    """Adding a feed does not excuse a broken X configuration."""
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: [nitter]
rss_sources:
  - id: visualnovel
    url: "https://example.com/interview.xml"
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "nitter.endpoints" in str(excinfo.value)


def test_nitter_endpoints_are_not_required_when_nitter_is_not_in_the_order(tmp_path):
    """``order: [xtf]`` must not be forced to carry Nitter endpoints."""
    path = write(
        tmp_path,
        "config.yaml",
        "provider:\n  order: [xtf]\n",
    )
    assert load_config(path).provider.order == ["xtf"]


def test_unknown_nitter_route_is_rejected(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  nitter:
    endpoints: [https://a.example]
    routes: [rss, telepathy]
""",
    )
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_defaults_are_sensible(tmp_path):
    # `Config()` with no provider block is intentionally rejected (see below),
    # so the smallest valid provider block is supplied here.
    config = Config(**{"provider": {"nitter": {"endpoints": ["https://a.example"]}}})
    assert config.provider.order == ["nitter", "xtf"]
    assert config.provider.nitter.routes == ["rss", "html"]
    assert config.http.timeout == 20
    assert config.fetch.limit == 20
    assert config.storage.data_dir == "data"
    assert config.storage.store_raw_response == "on_error"
    # xtf reuses the nitter endpoints when its own list is empty.
    assert config.provider.xtf.instances == []


def test_bare_default_config_is_rejected_because_it_cannot_fetch():
    """Defaults alone would advertise a provider with no endpoints, which is
    exactly the misconfiguration the validator exists to catch."""
    with pytest.raises(Exception):
        Config()


def test_storage_paths_derive_from_data_dir(tmp_path):
    config = Config(
        **{
            "provider": {"nitter": {"endpoints": ["https://a.example"]}},
            "storage": {"data_dir": str(tmp_path / "d")},
        }
    )
    assert config.storage.accounts_dir == tmp_path / "d" / "accounts"
    assert config.storage.runs_dir == tmp_path / "d" / "runs"
    assert config.storage.raw_dir == tmp_path / "d" / "raw"


def test_negative_limits_are_rejected():
    with pytest.raises(Exception):
        Config(
            **{
                "provider": {"nitter": {"endpoints": ["https://a.example"]}},
                "fetch": {"limit": 0},
            }
        )


# --- file-level failures --------------------------------------------------
def test_missing_config_file_raises_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(tmp_path / "nope.yaml")
    assert "not found" in str(excinfo.value)


def test_invalid_yaml_raises_configuration_error(tmp_path):
    path = write(tmp_path, "config.yaml", "provider: [unclosed\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "invalid YAML" in str(excinfo.value)


def test_non_mapping_yaml_raises_configuration_error(tmp_path):
    path = write(tmp_path, "config.yaml", "- just\n- a\n- list\n")
    with pytest.raises(ConfigurationError):
        load_config(path)


# --- accounts -------------------------------------------------------------
def test_accounts_accept_mapping_and_shorthand(tmp_path):
    path = write(
        tmp_path,
        "accounts.yaml",
        """
accounts:
  - jack
  - username: OpenAI
  - username: AnthropicAI
    enabled: false
""",
    )
    accounts = load_accounts(path)
    assert [a.username for a in accounts] == ["jack", "OpenAI", "AnthropicAI"]
    assert [a.enabled for a in accounts] == [True, True, False]


def test_leading_at_sign_is_normalised(tmp_path):
    path = write(tmp_path, "accounts.yaml", "accounts:\n  - '@jack'\n")
    assert load_accounts(path)[0].username == "jack"


def test_duplicate_accounts_are_rejected(tmp_path):
    path = write(tmp_path, "accounts.yaml", "accounts:\n  - jack\n  - JACK\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_accounts(path)
    assert "duplicate" in str(excinfo.value)


def test_empty_username_is_rejected(tmp_path):
    path = write(tmp_path, "accounts.yaml", "accounts:\n  - username: ''\n")
    with pytest.raises(ConfigurationError):
        load_accounts(path)


def test_missing_username_is_rejected(tmp_path):
    path = write(tmp_path, "accounts.yaml", "accounts:\n  - enabled: true\n")
    with pytest.raises(ConfigurationError):
        load_accounts(path)


def test_missing_accounts_key_is_rejected(tmp_path):
    path = write(tmp_path, "accounts.yaml", "something_else: true\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_accounts(path)
    assert "accounts" in str(excinfo.value)


def test_empty_account_list_is_accepted(tmp_path):
    """An RSS-only deployment has no X accounts to declare.

    This test used to be ``test_empty_account_list_is_rejected``. Phase 1 could
    assume every deployment had at least one account; Phase 2 cannot, because a
    deployment may read feeds only. The guard that used to live here now lives
    in ``Config`` (at least one enabled input source) and in ``main.py`` (at
    least one unit to process), which is a better place for it — it is about
    inputs, not about X.
    """
    path = write(tmp_path, "accounts.yaml", "accounts: []\n")
    assert load_accounts(path) == []


# --- YouTube input --------------------------------------------------------
# A third independent input family. Same rule as RSS: the family itself is
# neither privileged nor required, it just counts as an input source.
def test_youtube_only_config_is_accepted(tmp_path):
    """``order: []`` + one enabled channel, with no feed and no X."""
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: []
youtube_channels:
  - channel_id: UCabcdefghijklmnopqrstuv
""",
    )
    config = load_config(path)
    assert config.x_input_enabled is False
    assert config.enabled_rss_sources == []
    assert [c.channel_id for c in config.enabled_youtube_channels] == [
        "UCabcdefghijklmnopqrstuv"
    ]
    assert config.youtube_input_enabled is True
    # No Nitter endpoints were required, exactly as for RSS-only.
    assert config.provider.nitter.endpoints == []


def test_all_three_input_families_together_are_accepted(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL
        + """
rss_sources:
  - id: visualnovel
    url: "https://example.com/interview.xml"
youtube_channels:
  - channel_id: UCabcdefghijklmnopqrstuv
  - channel_id: UCzyxwvutsrqponmlkjihgfe
    enabled: false
""",
    )
    config = load_config(path)
    assert config.x_input_enabled is True
    assert [s.id for s in config.enabled_rss_sources] == ["visualnovel"]
    assert len(config.enabled_youtube_channels) == 1


def test_a_channel_id_must_not_be_empty(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        'provider:\n  order: []\nyoutube_channels:\n  - channel_id: ""\n',
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "must not be empty" in str(excinfo.value)


def test_a_pasted_channel_url_is_rejected_with_a_clear_message(tmp_path):
    """The single most likely operator mistake, caught at load time."""
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: []
youtube_channels:
  - channel_id: "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "looks like a URL" in str(excinfo.value)


def test_an_at_handle_is_rejected_as_a_channel_id(tmp_path):
    """A display handle is not an identity: it can be renamed at any time."""
    path = write(
        tmp_path,
        "config.yaml",
        'provider:\n  order: []\nyoutube_channels:\n  - channel_id: "@SomeChannel"\n',
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "channel_id" in str(excinfo.value)


def test_duplicate_channel_ids_are_rejected(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: []
youtube_channels:
  - channel_id: UCabcdefghijklmnopqrstuv
  - channel_id: UCABCDEFGHIJKLMNOPQRSTUV
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "duplicate youtube channel_id" in str(excinfo.value)


def test_a_disabled_channel_is_not_an_input_source(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: []
youtube_channels:
  - channel_id: UCabcdefghijklmnopqrstuv
    enabled: false
""",
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "no input source" in str(excinfo.value)


def test_a_channel_url_override_must_be_http(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: []
youtube_channels:
  - channel_id: UCabcdefghijklmnopqrstuv
    url: "ftp://example.com/feed.xml"
""",
    )
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_channel_id_whitespace_is_stripped(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        """
provider:
  order: []
youtube_channels:
  - channel_id: "  UCabcdefghijklmnopqrstuv  "
""",
    )
    config = load_config(path)
    assert config.youtube_channels[0].channel_id == "UCabcdefghijklmnopqrstuv"


def test_no_input_source_message_names_youtube_too(tmp_path):
    """The error must tell the operator about every family they could enable."""
    path = write(tmp_path, "config.yaml", "provider:\n  order: []\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert "no input source" in message
    assert "youtube_channels" in message


# --- QQ delivery ----------------------------------------------------------
def test_qq_is_off_by_default():
    config = Config(provider={"order": ["nitter"], "nitter": {"endpoints": ["https://a.example"]}})
    assert config.qq.enabled is False


def test_qq_enabled_requires_a_group_id(tmp_path):
    path = write(tmp_path, "config.yaml", MINIMAL + "\nqq:\n  enabled: true\n")
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "group_id" in str(excinfo.value)


def test_qq_enabled_requires_an_api_base(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL + '\nqq:\n  enabled: true\n  api_base: ""\n  group_id: 123456\n',
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "api_base" in str(excinfo.value)


def test_a_disabled_qq_block_may_be_incomplete(tmp_path):
    """Off is off: an incomplete block must not stop the run."""
    path = write(tmp_path, "config.yaml", MINIMAL + "\nqq:\n  enabled: false\n")
    config = load_config(path)
    assert config.qq.group_id == ""


def test_qq_group_id_accepts_an_unquoted_number(tmp_path):
    """QQ group ids are numbers, and YAML will write them unquoted."""
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL
        + "\nqq:\n  enabled: true\n  api_base: http://127.0.0.1:3000\n  group_id: 123456789\n",
    )
    config = load_config(path)
    assert config.qq.group_id == "123456789"
    assert isinstance(config.qq.group_id, str)


def test_qq_group_id_accepts_a_quoted_string(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL
        + '\nqq:\n  enabled: true\n  api_base: http://127.0.0.1:3000\n  group_id: "123456789"\n',
    )
    assert load_config(path).qq.group_id == "123456789"


def test_qq_api_base_trailing_slash_is_stripped(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL
        + "\nqq:\n  enabled: true\n  api_base: http://127.0.0.1:3000/\n  group_id: 1\n",
    )
    assert load_config(path).qq.api_base == "http://127.0.0.1:3000"


def test_qq_api_base_must_be_http(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL + '\nqq:\n  enabled: true\n  api_base: "127.0.0.1:3000"\n  group_id: 1\n',
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "api_base" in str(excinfo.value)


def test_qq_rejects_an_unknown_key(tmp_path):
    """A token written into the committed config must fail loudly, not silently.

    This is the same safety property Telegram has: pydantic ignores unknown keys
    by default, so without ``extra="forbid"`` a committed access token would be
    dropped on the floor and the operator would see "no token" while staring at
    one in the file.
    """
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL
        + '\nqq:\n  enabled: true\n  group_id: 1\n  access_token: "0123456789abcdef"\n',
    )
    with pytest.raises(ConfigurationError) as excinfo:
        load_config(path)
    assert "access_token" in str(excinfo.value)


def test_qq_access_token_env_names_the_variable_not_the_secret(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL
        + "\nqq:\n  enabled: true\n  group_id: 1\n  access_token_env: MY_ONEBOT_TOKEN\n",
    )
    config = load_config(path)
    assert config.qq.access_token_env == "MY_ONEBOT_TOKEN"
    # The value itself is nowhere on the config object.
    assert not hasattr(config.qq, "access_token")


def test_qq_access_token_env_defaults_to_the_documented_name(tmp_path):
    path = write(tmp_path, "config.yaml", MINIMAL + "\nqq:\n  enabled: true\n  group_id: 1\n")
    assert load_config(path).qq.access_token_env == "ONEBOT_ACCESS_TOKEN"


def test_qq_timeout_must_be_positive(tmp_path):
    path = write(
        tmp_path,
        "config.yaml",
        MINIMAL + "\nqq:\n  enabled: true\n  group_id: 1\n  timeout: 0\n",
    )
    with pytest.raises(ConfigurationError):
        load_config(path)


# --- the shipped files must actually load ---------------------------------
def test_shipped_config_loads():
    config = load_config(PROJECT_ROOT / "config" / "config.yaml")
    assert config.provider.nitter.endpoints
    # No endpoint may carry stray whitespace.
    assert all(e == e.strip() for e in config.provider.nitter.endpoints)
    assert all(not e.endswith("/") for e in config.provider.nitter.endpoints)


def test_shipped_accounts_load():
    accounts = load_accounts(PROJECT_ROOT / "accounts.yaml")
    assert accounts
    assert all(a.username and a.username == a.username.strip() for a in accounts)
