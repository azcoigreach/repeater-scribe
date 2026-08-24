from __future__ import annotations

from asl_transcriber.transcription.callsigns import CallsignResolver, callsign_hotwords


def test_resolver_converts_only_known_phonetic_callsign() -> None:
    resolver = CallsignResolver(("KM7GHS",))

    assert (
        resolver.resolve("This is Kilo Mike Seven Golf Hotel Sierra checking in")
        == "This is KM7GHS checking in"
    )
    assert resolver.resolve("Kilo Mike Seven Delta Hotel Sierra") == (
        "Kilo Mike Seven Delta Hotel Sierra"
    )


def test_hotwords_include_written_and_spoken_callsign() -> None:
    result = callsign_hotwords(["km7ghs"], "AllStar, Arizona")

    assert "KM7GHS" in result
    assert "Kilo Mike Seven Golf Hotel Sierra" in result
    assert "AllStar, Arizona" in result
