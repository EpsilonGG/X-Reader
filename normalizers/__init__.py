"""Normalizer layer: raw records -> the unified NormalizedItem contract.

The only layer that knows both a source's shape and the shared contract. Pure
functions: no network, no storage, no output formatting, no AI.
"""
from normalizers.base import BaseNormalizer
from normalizers.registry import KIND_RSS
from normalizers.registry import KIND_X
from normalizers.registry import KIND_YOUTUBE
from normalizers.registry import kinds
from normalizers.registry import normalizer_for
from normalizers.rss import RssNormalizer
from normalizers.rss import item_id_from_url
from normalizers.x import XNormalizer
from normalizers.youtube import YoutubeNormalizer

__all__ = [
    "BaseNormalizer",
    "XNormalizer",
    "RssNormalizer",
    "YoutubeNormalizer",
    "item_id_from_url",
    "normalizer_for",
    "kinds",
    "KIND_X",
    "KIND_RSS",
    "KIND_YOUTUBE",
]
