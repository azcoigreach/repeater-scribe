from __future__ import annotations

import hashlib
import time
from pathlib import Path


class FileStabilizer:
    def __init__(self, stable_seconds: float = 5.0, poll_interval: float = 0.25) -> None:
        self.stable_seconds = stable_seconds
        self.poll_interval = poll_interval

    def wait_for_stable(self, path: str | Path) -> bool:
        target = Path(path)
        if not target.exists():
            return False

        last_snapshot: tuple[int, int] | None = None
        end_time = time.monotonic() + self.stable_seconds

        while time.monotonic() < end_time:
            current = self._snapshot(target)
            if last_snapshot is not None and current != last_snapshot:
                end_time = time.monotonic() + self.stable_seconds
            last_snapshot = current
            time.sleep(self.poll_interval)

        return True

    @staticmethod
    def _snapshot(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns

    @staticmethod
    def hash_file_bytes(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()
