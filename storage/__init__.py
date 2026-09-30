"""Storage layer: the durable boundary.

Callers depend on ``BaseStorage`` (normalized items, runs, raw bodies, delivery
state) or ``RawRecordArchive`` (Phase 1 raw evidence). Both are interfaces, so a
future derived index (SQLite, Phase 3) can be introduced without touching the
runner, providers, parsers, normalizers or outputs.
"""
from storage.base import DELIVERY_FAILED
from storage.base import DELIVERY_PENDING
from storage.base import DELIVERY_SENT
from storage.base import DELIVERY_SKIPPED
from storage.base import BaseStorage
from storage.base import DeliveryRecord
from storage.base import RawRecordArchive
from storage.base import RunRecord
from storage.base import SaveReport
from storage.jsonl import JsonlStorage

__all__ = [
    "BaseStorage",
    "RawRecordArchive",
    "JsonlStorage",
    "RunRecord",
    "SaveReport",
    "DeliveryRecord",
    "DELIVERY_PENDING",
    "DELIVERY_SENT",
    "DELIVERY_FAILED",
    "DELIVERY_SKIPPED",
]
