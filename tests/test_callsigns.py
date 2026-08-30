from __future__ import annotations

from asl_transcriber.transcription.callsigns import (
    CallsignResolver,
    callsign_hotwords,
    extract_callsigns,
)


def test_resolver_converts_exact_and_nearby_known_phonetic_callsign() -> None:
    resolver = CallsignResolver(("KM7GHS",))

    assert (
        resolver.resolve("This is Kilo Mike Seven Golf Hotel Sierra checking in")
        == "This is KM7GHS checking in"
    )
    assert resolver.resolve("Kilo Mike Seven Delta Hotel Sierra") == "KM7GHS"


def test_hotwords_include_written_and_spoken_callsign() -> None:
    result = callsign_hotwords(["km7ghs"], "AllStar, Arizona")

    assert "KM7GHS" in result
    assert "Kilo Mike Seven Golf Hotel Sierra" in result
    assert "AllStar, Arizona" in result


def test_extract_callsigns_preserves_first_mention_order_and_removes_duplicates() -> None:
    assert extract_callsigns("K7ABC called km7ghs, then K7ABC again.") == ("K7ABC", "KM7GHS")


def test_resolver_repairs_observed_fast_speech_errors_from_local_candidates() -> None:
    resolver = CallsignResolver(("KM7GHS", "KE7WIL"))

    result = resolver.resolve_detailed("Back to you, AM7 VHS. Then KU7WIL.")

    assert result.text == "Back to you, KM7GHS. Then KE7WIL."
    assert [(item.original, item.corrected) for item in result.corrections] == [
        ("AM7 VHS", "KM7GHS"),
        ("KU7WIL", "KE7WIL"),
    ]
    assert all(item.confidence in {"high", "medium"} for item in result.corrections)


def test_resolver_uses_strong_local_prior_to_break_distant_candidate_tie() -> None:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    distractors = tuple(f"K7A{first}{second}" for first in "ABCD" for second in alphabet)
    resolver = CallsignResolver(("KM7GHS", *distractors, "KM7BHS"))

    assert resolver.resolve("He said AM7 VHS.") == "He said KM7GHS."


def test_resolver_normalizes_numeric_slot_and_split_suffix_without_candidate() -> None:
    resolver = CallsignResolver()

    assert resolver.resolve("KDIDJ checking in") == "KD1DJ checking in"
    assert resolver.resolve("Back to KG7 Oni") == "Back to KG7ONI"
    assert resolver.resolve("ARIES net") == "ARIES net"
    assert resolver.resolve("KG7 EZP. I agree") == "KG7EZP. I agree"
    assert resolver.resolve("KH6. The next station") == "KH6. The next station"
    assert resolver.resolve("WU2M, NQ2U") == "WU2M, NQ2U"
    assert resolver.resolve("NCO's report") == "NCO's report"


def test_resolver_does_not_guess_between_equally_close_candidates() -> None:
    resolver = CallsignResolver(("K7ABC", "K7ABD"))

    assert resolver.resolve("K7ABX testing") == "K7ABX testing"
