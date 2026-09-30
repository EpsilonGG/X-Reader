"""Kind -> normalizer bindings.

Same shape as ``app/registry.py`` and for the same reason: a Provider says where
bytes come from, a Parser says how to read them, a Normalizer says how to turn a
raw record into the shared contract. None of them may know about each other, so
the mapping has to live somewhere neutral.

The key is a **kind**, not a provider name. ``nitter`` and ``xtf`` are two
providers for one kind (``x``); a future ``youtube-rss`` provider would still be
kind ``youtube``. Binding on the provider name would let the same content acquire
two identities depending on which provider happened to answer first.
"""
from __future__ import annotations

from normalizers.base import BaseNormalizer
from normalizers.rss import RssNormalizer
from normalizers.x import XNormalizer
from normalizers.youtube import YoutubeNormalizer

KIND_X = "x"
KIND_RSS = "rss"
KIND_YOUTUBE = "youtube"

#: Normalizers are stateless and pure, so one instance per kind is enough.
_NORMALIZERS: dict[str, BaseNormalizer] = {
    KIND_X: XNormalizer(),
    KIND_RSS: RssNormalizer(),
    KIND_YOUTUBE: YoutubeNormalizer(),
}


def normalizer_for(kind: str) -> BaseNormalizer | None:
    """Return the normalizer for ``kind``, or ``None`` when unbound."""
    return _NORMALIZERS.get(kind)


def kinds() -> list[str]:
    """Every bound kind, sorted — an introspectable description of capability."""
    return sorted(_NORMALIZERS)
