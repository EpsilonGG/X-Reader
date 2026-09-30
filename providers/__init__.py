"""Provider layer: how X-Reader reaches its sources.

A provider returns a raw response and nothing more. Adding a source means adding
a module here plus its ``(provider, route)`` parser binding in
``app/registry.py``.

Two families, deliberately kept apart:

* the **X providers** (``nitter``, ``xtf``) form an ordered fallback chain for one
  kind of tracked unit — an X account;
* the **RSS provider** serves one tracked unit per configured feed URL. It is not
  in ``provider.order``, because a feed URL is the unit itself, not an
  alternative way to reach an account.
"""
from providers.base import BaseProvider
from providers.factory import build_providers
from providers.factory import build_rss_provider
from providers.nitter import NitterProvider
from providers.rss import ROUTE_FEED
from providers.rss import RssProvider
from providers.xtf_adapter import XtfAdapter

__all__ = [
    "BaseProvider",
    "NitterProvider",
    "RssProvider",
    "XtfAdapter",
    "build_providers",
    "build_rss_provider",
    "ROUTE_FEED",
]
