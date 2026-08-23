from __future__ import annotations

import wave
from pathlib import Path

from asl_transcriber.audio.probe import AudioProbeResult, probe_audio
from asl_transcriber.transcription.base import (
    TranscriptionEngine,
    TranscriptResult,
    TranscriptSegment,
)
from asl_transcriber.transcription.faster_whisper import FasterWhisperEngine


def write_pcm_wav(path: Path, sample_rate: int = 8000, seconds: float = 0.25) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = int(sample_rate * seconds)
        samples = bytearray()
        for i in range(frames):
            value = max(-32768, min(32767, int(2000 * i / max(1, frames))))
            samples.extend(value.to_bytes(2, byteorder="little", signed=True))
        handle.writeframes(bytes(samples))


def test_audio_probe_reads_wave_metadata(tmp_path) -> None:
    target = tmp_path / "audio" / "sample.wav"
    write_pcm_wav(target)

    probe = probe_audio(target)

    assert isinstance(probe, AudioProbeResult)
    assert probe.sample_rate == 8000
    assert probe.channels == 1
    assert probe.duration_seconds > 0
    assert probe.file_size > 0


def test_transcription_engine_contract_returns_segments() -> None:
    class DemoEngine(TranscriptionEngine):
        def transcribe(self, path: str) -> TranscriptResult:
            return TranscriptResult(
                raw_text="hello radio",
                display_text="hello radio",
                language="en",
                segments=[
                    TranscriptSegment(
                        start=0.0,
                        end=1.0,
                        text="hello radio",
                        language="en",
                    )
                ],
            )

    engine = DemoEngine()
    result = engine.transcribe("/tmp/example.wav")

    assert isinstance(result, TranscriptResult)
    assert len(result.segments) == 1
    assert result.segments[0].text == "hello radio"


def test_faster_whisper_engine_uses_mocked_model(monkeypatch, tmp_path) -> None:
    target = tmp_path / "audio.wav"
    write_pcm_wav(target)

    class FakeModel:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def transcribe(self, *args, **kwargs):
            return {
                "segments": [
                    {"start": 0.0, "end": 1.0, "text": "hello radio", "language": "en"},
                ],
                "language": "en",
            }

    monkeypatch.setattr("asl_transcriber.transcription.faster_whisper.WhisperModel", FakeModel)

    engine = FasterWhisperEngine(model_size="tiny.en", device="cpu")
    result = engine.transcribe(str(target))

    assert result.raw_text == "hello radio"
    assert result.display_text == "hello radio"
    assert result.language == "en"
    assert result.segments[0].text == "hello radio"
