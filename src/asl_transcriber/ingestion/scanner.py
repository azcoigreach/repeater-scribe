from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

SUPPORTED_EXTENSIONS = {".wav", ".wav49"}


@dataclass(frozen=True)
class ArchiveEntry:
    source_path: str
    absolute_path: Path
    size_bytes: int
    modified_ns: int


class ArchiveScanner:
    def __init__(self, root: str | Path, *, retention_days: int = 0) -> None:
        self.root = Path(root)
        self.retention_days = retention_days

    def discover(self) -> list[ArchiveEntry]:
        if not self.root.exists():
            return []

        entries: list[ArchiveEntry] = []
        for path in sorted(self.root.rglob("*")):
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                continue

            name = path.name
            if name.startswith((".", "~")):
                continue
            if any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue
            if "tmp" in name.lower() or name.lower().endswith(".tmp"):
                continue
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if self.retention_days > 0:
                cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
                if stat.st_mtime < cutoff.timestamp():
                    continue

            entries.append(
                ArchiveEntry(
                    source_path=path.relative_to(self.root).as_posix(),
                    absolute_path=path,
                    size_bytes=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                )
            )

        return entries
