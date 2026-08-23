from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter
from typing import Any

from asl_transcriber.transcription.base import TranscriptResult, TranscriptSegment

WhisperModel: Any | None = None
try:
    from faster_whisper import WhisperModel as _WhisperModel  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - used when dependency is not installed
    pass
else:
    WhisperModel = _WhisperModel


@dataclass
class FasterWhisperEngine:
    model_size: str = "tiny.en"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = None
    beam_size: int = 5
    vad_filter: bool = False
    initial_prompt: str | None = None
    word_timestamps: bool = False
    workers: int = 1
    model_dir: str | None = None
    _model: Any | None = field(default=None, init=False, repr=False)
    _model_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        model_class = WhisperModel
        if model_class is None:
            raise RuntimeError("faster-whisper is not installed")
        with self._model_lock:
            if self._model is None:
                self._model = model_class(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=self.model_dir,
                    cpu_threads=self.workers,
                )
        return self._model

    def transcribe(self, path: str) -> TranscriptResult:
        if WhisperModel is None:
            raise RuntimeError("faster-whisper is not installed")

        start = perf_counter()
        model = self._get_model()
        raw_result = model.transcribe(
            path,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=self.vad_filter,
            initial_prompt=self.initial_prompt,
            word_timestamps=self.word_timestamps,
        )

        if isinstance(raw_result, tuple):
            segments_result, info = raw_result
        else:
            info = raw_result if isinstance(raw_result, dict) else {}
            segments_result = info.get("segments", [])

        def segment_value(segment: Any, key: str, default: Any = None) -> Any:
            if isinstance(segment, dict):
                return segment.get(key, default)
            return getattr(segment, key, default)

        segments = [
            TranscriptSegment(
                start=float(segment_value(segment, "start", 0.0)),
                end=float(segment_value(segment, "end", 0.0)),
                text=str(segment_value(segment, "text", "")).strip(),
                language=segment_value(segment, "language") or self.language,
                confidence=segment_value(segment, "avg_logprob"),
            )
            for segment in segments_result
        ]
        transcript_text = " ".join(segment.text for segment in segments).strip()
        duration = perf_counter() - start

        return TranscriptResult(
            raw_text=transcript_text,
            display_text=transcript_text,
            language=info.get("language") if isinstance(info, dict) else self.language,
            language_probability=info.get("language_probability") if isinstance(info, dict) else None,
            confidence=None,
            segments=segments,
            engine_name="faster-whisper",
            engine_version=getattr(WhisperModel, "__version__", "unknown"),
            model_name=self.model_size,
            processing_time_seconds=duration,
            options={
                "device": self.device,
                "compute_type": self.compute_type,
                "language": self.language,
                "beam_size": self.beam_size,
                "vad_filter": self.vad_filter,
                "word_timestamps": self.word_timestamps,
                "workers": self.workers,
            },
        )
