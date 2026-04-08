"""Megahub — local-first agent coordination hub. Zero dependencies beyond Python 3.10+."""

from .client import MegahubClient, MegahubError
from megahub_single import HubConfig, create_server, ensure_hub, run_server

__version__ = "0.1.0"
__all__ = ["MegahubClient", "MegahubError", "HubConfig", "create_server", "ensure_hub", "run_server"]
