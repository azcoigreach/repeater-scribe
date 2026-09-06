from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    language: str | None = None
    confidence: float | None = None
    ordinal: int = 0
    raw_text: str | None = None
    display_text: str | None = None


@dataclass(frozen=True)
class TranscriptCallsignMention:
    callsign: str
    start: float
    end: float
    confidence: float = 0.5
    acoustic_confidence: float | None = None
    recognition_confidence: float = 0.5
    evidence: tuple[str, ...] = ()
    raw_observed_value: str | None = None
    recognition_method: str = "legacy"
    timing_precision: str = "segment"


@dataclass
class TranscriptResult:
    raw_text: str
    display_text: str
    language: str | None = None
    language_probability: float | None = None
    confidence: float | None = None
    segments: list[TranscriptSegment] = field(default_factory=list)
    callsign_mentions: list[TranscriptCallsignMention] = field(default_factory=list)
    engine_name: str | None = None
    engine_version: str | None = None
    model_name: str | None = None
    processing_time_seconds: float | None = None
    options: dict[str, object] = field(default_factory=dict)


class TranscriptionEngine(Protocol):
    def transcribe(self, path: str) -> TranscriptResult:
        """Return a structured transcript result for a recording path."""
        ...
