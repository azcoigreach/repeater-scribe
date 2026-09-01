from __future__ import annotations

import logging
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from asl_transcriber.runtime import ArchiveRuntime
from asl_transcriber.transcription.base import TranscriptResult

logger = logging.getLogger(__name__)


def _find_redecoded_tail_start(previous_keys: list[str], current_keys: list[str]) -> int | None:
    """Find where a revised rolling window begins in accumulated text."""
    lookback = max(80, len(current_keys) * 2)
    first_start = max(0, len(previous_keys) - lookback)
    best: tuple[int, float, int, int] | None = None

    for previous_start in range(first_start, len(previous_keys) - 2):
        previous_tail = previous_keys[previous_start:]
        length_slack = max(6, len(previous_tail) // 3)
        minimum_prefix = max(3, len(previous_tail) - length_slack)
        maximum_prefix = min(len(current_keys), len(previous_tail) + length_slack)
        for prefix_length in range(minimum_prefix, maximum_prefix + 1):
            matcher = SequenceMatcher(
                None,
                previous_tail,
                current_keys[:prefix_length],
                autojunk=False,
            )
            matching_words = sum(block.size for block in matcher.get_matching_blocks())
            similarity = matcher.ratio()
            if matching_words < 3 or similarity < 0.58:
                continue

            candidate = (matching_words, similarity, prefix_length, -previous_start)
            if best is None or candidate > best:
                best = candidate

    return -best[3] if best is not None else None


def merge_overlapping_text(previous: str, current: str) -> str:
    """Merge a rolling-window transcript into the provisional transmission text."""
    previous_words = previous.split()
    current_words = current.split()
    if not previous_words:
        return current.strip()
    if not current_words:
        return previous.strip()

    previous_keys = [word.casefold().strip(".,!?;:\"'()[]") for word in previous_words]
    current_keys = [word.casefold().strip(".,!?;:\"'()[]") for word in current_words]
    maximum = min(len(previous_keys), len(current_keys))
    for overlap in range(maximum, 1, -1):
        if previous_keys[-overlap:] == current_keys[:overlap]:
            return " ".join(previous_words + current_words[overlap:]).strip()

    if " ".join(current_keys) in " ".join(previous_keys):
        return previous.strip()

    redecoded_tail_start = _find_redecoded_tail_start(previous_keys, current_keys)
    if redecoded_tail_start is not None:
        return " ".join(previous_words[:redecoded_tail_start] + current_words).strip()

    return " ".join(previous_words + current_words).strip()


@dataclass
class FfmpegSnapshotter:
    tmp_dir: Path
    window_seconds: float = 12.0
    ffmpeg_binary: str = "ffmpeg"
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def snapshot(self, source: Path) -> Path:
        with tempfile.NamedTemporaryFile(
            prefix="repeater-scribe-live-", suffix=".wav", dir=self.tmp_dir, delete=False
        ) as handle:
            target = Path(handle.name)
        command = [
            self.ffmpeg_binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-sseof",
            f"-{self.window_seconds:g}",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(target),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return target


@dataclass
class LiveTranscriptionService:
    snapshotter: FfmpegSnapshotter
    transcribe: Callable[[str], TranscriptResult]
    min_file_bytes: int = 4096
    max_files_per_cycle: int = 1
    _last_sizes: dict[str, int] = field(default_factory=dict, init=False)
    _texts: dict[str, str] = field(default_factory=dict, init=False)

    def process_once(self, runtime: ArchiveRuntime) -> int:
        waiting = set(runtime.waiting_sources())
        for stale in set(self._last_sizes) - waiting:
            self._last_sizes.pop(stale, None)
            self._texts.pop(stale, None)
            runtime.clear_live_result(stale)

        candidates: list[tuple[int, str, Path, int]] = []
        for source_path in waiting:
            try:
                source = runtime._resolve_source(source_path)
                source_stat = source.stat()
                size = source_stat.st_size
            except (FileNotFoundError, OSError):
                continue
            if size < self.min_file_bytes or self._last_sizes.get(source_path) == size:
                continue
            candidates.append((source_stat.st_mtime_ns, source_path, source, size))

        processed = 0
        for _, source_path, source, size in sorted(candidates, reverse=True)[
            : self.max_files_per_cycle
        ]:
            self._last_sizes[source_path] = size
            snapshot: Path | None = None
            try:
                snapshot = self.snapshotter.snapshot(source)
                result = self.transcribe(str(snapshot))
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
                logger.debug("Live transcription skipped for %s: %s", source_path, error)
                continue
            finally:
                if snapshot is not None:
                    snapshot.unlink(missing_ok=True)

            merged = merge_overlapping_text(self._texts.get(source_path, ""), result.display_text)
            self._texts[source_path] = merged
            runtime.set_live_result(source_path, result, display_text=merged)
            processed += 1
        return processed
