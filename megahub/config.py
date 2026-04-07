from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path


LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(slots=True)
class HubConfig:
    listen_host: str = "127.0.0.1"
    port: int = 8765
    allow_remote: bool = False
    storage_path: str = "megahub.sqlite3"
    log_events: bool = True
    presence_ttl_sec: int = 120
    max_body_chars: int = 16_000
    max_attachment_chars: int = 32_000
    max_attachments: int = 16
    max_query_limit: int = 500

    def validate(self) -> None:
        if self.port < 0 or self.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.presence_ttl_sec < 5:
            raise ValueError("presence_ttl_sec must be at least 5")
        if self.max_body_chars < 1:
            raise ValueError("max_body_chars must be at least 1")
        if self.max_attachment_chars < 1:
            raise ValueError("max_attachment_chars must be at least 1")
        if self.max_attachments < 0:
            raise ValueError("max_attachments must be non-negative")
        if self.max_query_limit < 1:
            raise ValueError("max_query_limit must be at least 1")
        if not self.allow_remote and self.listen_host not in LOCAL_BIND_HOSTS:
            raise ValueError(
                "Remote bind requested while allow_remote=false. "
                "Use a localhost bind or set allow_remote=true."
            )
        storage_path = Path(self.storage_path).expanduser()
        if not storage_path.is_absolute():
            storage_path = (Path.cwd() / storage_path)
        storage_path = storage_path.resolve()
        if storage_path.exists() and storage_path.is_dir():
            raise ValueError("storage_path must point to a file, not a directory")
        try:
            storage_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"unable to create storage directory: {storage_path.parent}") from exc
        try:
            if storage_path.exists():
                with storage_path.open("ab"):
                    pass
            else:
                with tempfile.NamedTemporaryFile(dir=storage_path.parent, prefix=".megahub-write-check-", delete=True):
                    pass
        except OSError as exc:
            raise ValueError(f"storage_path is not writable: {storage_path}") from exc
