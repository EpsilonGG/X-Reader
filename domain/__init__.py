"""Domain layer: models and error taxonomy. No I/O, no network, no config."""
from domain.errors import AllProvidersFailed
from domain.errors import ConfigurationError
from domain.errors import NetworkError
from domain.errors import ParsingError
from domain.errors import ProviderError
from domain.errors import StorageError
from domain.errors import XReaderError

__all__ = [
    "AllProvidersFailed",
    "ConfigurationError",
    "NetworkError",
    "ParsingError",
    "ProviderError",
    "StorageError",
    "XReaderError",
]
