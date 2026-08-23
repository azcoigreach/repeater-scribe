from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".wav", ".wav49"}


@dataclass(frozen=True)
class ArchiveEntry:
    source_path: str
    absolute_path: Path
    size_bytes: int
    modified_ns: int


class ArchiveScanner:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def discover(self) -> list[ArchiveEntry]:
        if not self.root.exists():
            return []

        entries: list[ArchiveEntry] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
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

            entries.append(
                ArchiveEntry(
                    source_path=path.relative_to(self.root).as_posix(),
                    absolute_path=path,
                    size_bytes=path.stat().st_size,
                    modified_ns=path.stat().st_mtime_ns,
                )
            )

        return entries
