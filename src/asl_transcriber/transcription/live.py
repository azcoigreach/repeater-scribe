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

    match = SequenceMatcher(None, previous_keys, current_keys, autojunk=False).find_longest_match()
    near_previous_end = len(previous_keys) - (match.a + match.size) <= 2
    near_current_start = match.b <= 2
    if match.size >= 3 and near_previous_end and near_current_start:
        return " ".join(previous_words + current_words[match.b + match.size :]).strip()
    if " ".join(current_keys) in " ".join(previous_keys):
        return previous.strip()
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
    _last_sizes: dict[str, int] = field(default_factory=dict, init=False)
    _texts: dict[str, str] = field(default_factory=dict, init=False)

    def process_once(self, runtime: ArchiveRuntime) -> int:
        waiting = set(runtime.waiting_sources())
        for stale in set(self._last_sizes) - waiting:
            self._last_sizes.pop(stale, None)
            self._texts.pop(stale, None)
            runtime.clear_live_result(stale)

        processed = 0
        for source_path in sorted(waiting):
            try:
                source = runtime._resolve_source(source_path)
                size = source.stat().st_size
            except (FileNotFoundError, OSError):
                continue
            if size < self.min_file_bytes or self._last_sizes.get(source_path) == size:
                continue
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
