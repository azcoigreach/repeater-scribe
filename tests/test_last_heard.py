from __future__ import annotations

from types import SimpleNamespace

from asl_transcriber.main import last_heard_callsigns
from asl_transcriber.qrz import QrzCallsign
from asl_transcriber.transcription.base import TranscriptCallsignMention


class FakeQrzClient:
    def lookup(self, callsign: str) -> QrzCallsign:
        return QrzCallsign(
            callsign=callsign,
            location="Mesa, AZ, United States",
            image_url=f"https://files.qrz.com/{callsign}.jpg",
            profile_url=f"https://www.qrz.com/db/{callsign}",
        )


def test_last_heard_callsigns_are_extracted_and_enriched(monkeypatch) -> None:
    job = SimpleNamespace(id="job-1", source_path="100000/2026083012304500-call.wav")
    result = SimpleNamespace(display_text="Kilo station KM7GHS checking in")
    runtime = SimpleNamespace(live_results={}, results={job.id: result}, jobs=lambda: [job])
    monkeypatch.setattr("asl_transcriber.main.current_runtime", lambda: runtime)
    monkeypatch.setattr("asl_transcriber.main.current_qrz_client", lambda: FakeQrzClient())

    response = last_heard_callsigns()

    assert response["configured"] is True
    assert response["items"] == [
        {
            "callsign": "KM7GHS",
            "last_heard_at": "2026-08-30T12:30:45+00:00",
            "source_path": "100000/2026083012304500-call.wav",
            "name": None,
            "location": "Mesa, AZ, United States",
            "image_url": "https://files.qrz.com/KM7GHS.jpg",
            "profile_url": "https://www.qrz.com/db/KM7GHS",
            "status": "found",
        }
    ]


def test_last_heard_uses_each_callsigns_latest_segment_time(monkeypatch) -> None:
    job = SimpleNamespace(id="job-1", source_path="100000/2026083012304500-call.wav")
    result = SimpleNamespace(
        display_text="KM7GHS then KE7WIL and KM7GHS again",
        callsign_mentions=[
            TranscriptCallsignMention("KM7GHS", 4.0, 5.5),
            TranscriptCallsignMention("KE7WIL", 16.0, 17.25),
            TranscriptCallsignMention("KM7GHS", 28.0, 29.75),
        ],
    )
    runtime = SimpleNamespace(live_results={}, results={job.id: result}, jobs=lambda: [job])
    monkeypatch.setattr("asl_transcriber.main.current_runtime", lambda: runtime)
    monkeypatch.setattr("asl_transcriber.main.current_qrz_client", lambda: None)

    response = last_heard_callsigns()

    assert response["items"] == [
        {
            "callsign": "KM7GHS",
            "last_heard_at": "2026-08-30T12:31:14.750000+00:00",
            "heard_offset_seconds": 29.75,
            "time_precision": "segment",
            "source_path": "100000/2026083012304500-call.wav",
        },
        {
            "callsign": "KE7WIL",
            "last_heard_at": "2026-08-30T12:31:02.250000+00:00",
            "heard_offset_seconds": 17.25,
            "time_precision": "segment",
            "source_path": "100000/2026083012304500-call.wav",
        },
    ]
