from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from threading import Lock
from time import perf_counter
from typing import Any

from asl_transcriber.transcription.base import (
    TranscriptCallsignMention,
    TranscriptResult,
    TranscriptSegment,
)
from asl_transcriber.transcription.callsigns import (
    CallsignResolver,
    callsign_hotwords,
    find_callsigns,
)

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
    callsign_provider: Callable[[], tuple[str, ...]] | None = None
    callsign_hotword_limit: int = 0
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
        resolver = self.callsign_resolver
        selected_hotwords = self.hotwords if use_hotwords else None
        if self.callsign_provider is not None:
            dynamic_callsigns = self.callsign_provider()
            resolver = CallsignResolver(dynamic_callsigns)
            selected_hotwords = None
            if use_hotwords and (self.callsign_hotword_limit > 0 or self.hotwords):
                selected_hotwords = callsign_hotwords(
                    dynamic_callsigns[: self.callsign_hotword_limit], self.hotwords
                )
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
                    ordinal=ordinal,
                    start=float(segment_value(segment, "start", 0.0)),
                    end=float(segment_value(segment, "end", 0.0)),
                    text=str(segment_value(segment, "text", "")).strip(),
                    language=segment_value(segment, "language") or self.language,
                    confidence=segment_value(segment, "avg_logprob"),
                )
                for ordinal, segment in enumerate(segments_result)
            ], info

        with self._inference_lock:
            segments, info = decode(selected_vad_filter)
        transcript_text = " ".join(segment.text for segment in segments).strip()
        resolution = resolver.resolve_detailed(transcript_text) if resolver else None
        display_text = resolution.text if resolution else transcript_text
        callsign_mentions: list[TranscriptCallsignMention] = []
        for segment in segments:
            segment_resolution = resolver.resolve_detailed(segment.text) if resolver else None
            resolved_segment = segment_resolution.text if segment_resolution else segment.text
            segment.raw_text = segment.text
            segment.display_text = resolved_segment
            raw_callsigns = find_callsigns(segment.text)
            acoustic_confidence = (
                max(0.05, min(0.98, math.exp(segment.confidence)))
                if segment.confidence is not None
                else 0.65
            )
            for callsign in find_callsigns(resolved_segment):
                corrections = [
                    correction
                    for correction in (segment_resolution.corrections if segment_resolution else ())
                    if correction.corrected == callsign
                ]
                evidence: list[str] = []
                if corrections:
                    correction = corrections[-1]
                    recognition_confidence = (
                        0.84 if correction.confidence == "high" else 0.7
                    )
                    evidence.append(f'Recovered from "{correction.original}"')
                    evidence.append(correction.reason)
                elif callsign in raw_callsigns:
                    recognition_confidence = 0.92
                    evidence.append("Decoded directly as a formatted callsign")
                else:
                    recognition_confidence = 0.78
                    evidence.append("Recovered by callsign grammar")
                if resolver and callsign in resolver.known_callsigns:
                    recognition_confidence = min(0.96, recognition_confidence + 0.04)
                    evidence.append("Matched previously known callsign")
                confidence = (acoustic_confidence * 0.55) + (
                    recognition_confidence * 0.45
                )
                callsign_mentions.append(
                    TranscriptCallsignMention(
                        callsign=callsign,
                        start=segment.start,
                        end=segment.end,
                        confidence=confidence,
                        acoustic_confidence=acoustic_confidence,
                        recognition_confidence=recognition_confidence,
                        evidence=tuple(evidence),
                        raw_observed_value=(corrections[-1].original if corrections else callsign),
                        recognition_method=(
                            "direct" if callsign in raw_callsigns else (
                                "grammar" if corrections and corrections[-1].reason == "callsign formatting"
                                else "phonetic"
                            )
                        ),
                    )
                )

        mention_counts = Counter(mention.callsign for mention in callsign_mentions)
        callsign_mentions = [
            replace(
                mention,
                confidence=min(
                    0.98,
                    mention.confidence
                    + min(0.1, max(0, mention_counts[mention.callsign] - 1) * 0.035),
                ),
                evidence=mention.evidence
                + (
                    (f"Repeated {mention_counts[mention.callsign]} times in this recording",)
                    if mention_counts[mention.callsign] > 1
                    else ()
                ),
            )
            for mention in callsign_mentions
        ]
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
            callsign_mentions=callsign_mentions,
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
                "callsign_corrections": (
                    [
                        {
                            "original": correction.original,
                            "corrected": correction.corrected,
                            "confidence": correction.confidence,
                            "reason": correction.reason,
                        }
                        for correction in resolution.corrections
                    ]
                    if resolution
                    else []
                ),
                "workers": self.workers,
            },
        )
