from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import perf_counter
from typing import Any

from asl_transcriber.transcription.base import TranscriptResult, TranscriptSegment
from asl_transcriber.transcription.callsigns import CallsignResolver

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
    hotwords: str | None = None
    word_timestamps: bool = False
    condition_on_previous_text: bool = True
    workers: int = 1
    model_dir: str | None = None
    callsign_resolver: CallsignResolver | None = None
    _model: Any | None = field(default=None, init=False, repr=False)
    _model_lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _inference_lock: Lock = field(default_factory=Lock, init=False, repr=False)

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

    def transcribe(
        self,
        path: str,
        *,
        beam_size: int | None = None,
        vad_filter: bool | None = None,
        condition_on_previous_text: bool | None = None,
        use_hotwords: bool = True,
    ) -> TranscriptResult:
        if WhisperModel is None:
            raise RuntimeError("faster-whisper is not installed")

        start = perf_counter()
        model = self._get_model()
        selected_beam_size = self.beam_size if beam_size is None else beam_size
        selected_vad_filter = self.vad_filter if vad_filter is None else vad_filter
        selected_hotwords = self.hotwords if use_hotwords else None
        selected_conditioning = (
            self.condition_on_previous_text
            if condition_on_previous_text is None
            else condition_on_previous_text
        )

        def segment_value(segment: Any, key: str, default: Any = None) -> Any:
            if isinstance(segment, dict):
                return segment.get(key, default)
            return getattr(segment, key, default)

        def decode(active_vad_filter: bool) -> tuple[list[TranscriptSegment], Any]:
            raw_result = model.transcribe(
                path,
                language=self.language,
                beam_size=selected_beam_size,
                vad_filter=active_vad_filter,
                initial_prompt=self.initial_prompt,
                hotwords=selected_hotwords,
                word_timestamps=self.word_timestamps,
                condition_on_previous_text=selected_conditioning,
            )

            if isinstance(raw_result, tuple):
                segments_result, info = raw_result
            else:
                info = raw_result if isinstance(raw_result, dict) else {}
                segments_result = info.get("segments", [])
            return [
                TranscriptSegment(
                    start=float(segment_value(segment, "start", 0.0)),
                    end=float(segment_value(segment, "end", 0.0)),
                    text=str(segment_value(segment, "text", "")).strip(),
                    language=segment_value(segment, "language") or self.language,
                    confidence=segment_value(segment, "avg_logprob"),
                )
                for segment in segments_result
            ], info

        with self._inference_lock:
            segments, info = decode(selected_vad_filter)
        transcript_text = " ".join(segment.text for segment in segments).strip()
        display_text = (
            self.callsign_resolver.resolve(transcript_text)
            if self.callsign_resolver is not None
            else transcript_text
        )
        duration = perf_counter() - start

        def info_value(key: str) -> Any:
            if isinstance(info, dict):
                return info.get(key)
            return getattr(info, key, None)

        return TranscriptResult(
            raw_text=transcript_text,
            display_text=display_text,
            language=info_value("language") or self.language,
            language_probability=info_value("language_probability"),
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
                "beam_size": selected_beam_size,
                "vad_filter": selected_vad_filter,
                "word_timestamps": self.word_timestamps,
                "condition_on_previous_text": selected_conditioning,
                "hotwords": selected_hotwords,
                "workers": self.workers,
            },
        )
