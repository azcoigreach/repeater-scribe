from __future__ import annotations

from types import SimpleNamespace

from asl_transcriber.main import (
    callsign_confidence_label,
    callsign_confidence_score,
    last_heard_callsigns,
)
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

    def cached_status(self, callsign: str) -> str | None:
        return "found" if callsign == "KM7GHS" else None


class FilteringQrzClient(FakeQrzClient):
    def lookup(self, callsign: str) -> QrzCallsign:
        if callsign == "W3UWU":
            return QrzCallsign(callsign=callsign, status="not_found")
        return super().lookup(callsign)


def test_last_heard_callsigns_are_extracted_and_enriched(monkeypatch) -> None:
    job = SimpleNamespace(id="job-1", source_path="100000/2026083012304500-call.wav")
    result = SimpleNamespace(display_text="Kilo station KM7GHS checking in")
    runtime = SimpleNamespace(live_results={}, results={job.id: result}, jobs=lambda: [job])
    monkeypatch.setattr("asl_transcriber.main.current_runtime", lambda: runtime)
    monkeypatch.setattr("asl_transcriber.main.current_qrz_client", lambda: FakeQrzClient())

    response = last_heard_callsigns()

    assert response["configured"] is True
    item = response["items"][0]
    assert item["callsign"] == "KM7GHS"
    assert item["last_heard_at"] == "2026-08-30T12:30:45+00:00"
    assert item["status"] == "found"
    assert item["confidence_percent"] == 64
    assert item["confidence_label"] == "Tentative"
    assert item["observation_count"] == 1
    assert item["recording_count"] == 1
    assert item["evidence"] == [
        "QRZ confirms this callsign exists",
        "Older transcript without saved acoustic evidence",
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

    items = response["items"]
    assert [item["callsign"] for item in items] == ["KM7GHS", "KE7WIL"]
    assert items[0]["last_heard_at"] == "2026-08-30T12:31:14.750000+00:00"
    assert items[0]["observation_count"] == 2
    assert items[0]["confidence_percent"] > items[1]["confidence_percent"]
    assert items[1]["last_heard_at"] == "2026-08-30T12:31:02.250000+00:00"


def test_last_heard_omits_candidates_not_found_by_qrz(monkeypatch) -> None:
    job = SimpleNamespace(id="job-1", source_path="100000/2026083012304500-call.wav")
    result = SimpleNamespace(display_text="W3UWU then KM7GHS", callsign_mentions=[])
    runtime = SimpleNamespace(live_results={}, results={job.id: result}, jobs=lambda: [job])
    monkeypatch.setattr("asl_transcriber.main.current_runtime", lambda: runtime)
    monkeypatch.setattr(
        "asl_transcriber.main.current_qrz_client", lambda: FilteringQrzClient()
    )

    response = last_heard_callsigns()

    assert response["total"] == 1
    assert response["rejected"] == 1
    assert [item["callsign"] for item in response["items"]] == ["KM7GHS"]


def test_confidence_increases_with_independent_evidence_and_qrz() -> None:
    single = callsign_confidence_score(0.65, 1, 1)
    repeated = callsign_confidence_score(0.65, 5, 1)
    independent = callsign_confidence_score(0.65, 5, 3)
    qrz_confirmed = callsign_confidence_score(0.65, 5, 3, qrz_confirmed=True)

    assert single < repeated < independent < qrz_confirmed
    assert callsign_confidence_label(single) == "Tentative"
    assert callsign_confidence_label(qrz_confirmed) == "High confidence"
