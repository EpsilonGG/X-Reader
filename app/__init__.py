"""Application layer: wiring, orchestration and reporting.

This is the only package allowed to know about *all* the others. Providers know
about HTTP, parsers know about markup, normalizers know about the shared
contract, storage knows about files — the app layer is what composes them into a
run, and ``app/registry.py`` is where the ``(provider, route)`` binding between
them is written down.
"""
from app.registry import Binding
from app.registry import bindings
from app.registry import kinds
from app.registry import normalizer_kind_for
from app.registry import parser_for
from app.registry import unbound_routes
from app.runner import KIND_RSS
from app.runner import KIND_X
from app.runner import STATUS_ERROR
from app.runner import STATUS_NO_TWEETS
from app.runner import STATUS_OK
from app.runner import STATUS_SKIPPED
from app.runner import AccountOutcome
from app.runner import Runner
from app.runner import RunSummary

__all__ = [
    "Runner",
    "RunSummary",
    "AccountOutcome",
    "Binding",
    "parser_for",
    "normalizer_kind_for",
    "bindings",
    "kinds",
    "unbound_routes",
    "STATUS_OK",
    "STATUS_NO_TWEETS",
    "STATUS_ERROR",
    "STATUS_SKIPPED",
    "KIND_X",
    "KIND_RSS",
]
