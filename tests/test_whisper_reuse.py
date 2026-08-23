from __future__ import annotations

from asl_transcriber.transcription.faster_whisper import FasterWhisperEngine


def test_engine_reuses_loaded_model(monkeypatch) -> None:
    created = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))

        def transcribe(self, path, **kwargs):
            return {"segments": [], "language": "en"}

    monkeypatch.setattr("asl_transcriber.transcription.faster_whisper.WhisperModel", FakeModel)
    engine = FasterWhisperEngine(model_size="tiny.en")

    engine.transcribe("first.wav")
    engine.transcribe("second.wav")

    assert len(created) == 1
