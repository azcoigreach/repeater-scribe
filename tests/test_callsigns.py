from __future__ import annotations

from asl_transcriber.transcription.callsigns import (
    CallsignResolver,
    callsign_hotwords,
    extract_callsigns,
    find_callsigns,
    normalize_callsigns,
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
    assert find_callsigns("K7ABC called KM7GHS, then K7ABC again.") == (
        "K7ABC",
        "KM7GHS",
        "K7ABC",
    )


def test_international_and_portable_callsigns_are_normalized_and_extracted() -> None:
    assert normalize_callsigns(("3DA0RS", "9A1A", "K7ABC/P", "F/AB1C")) == (
        "3DA0RS",
        "9A1A",
        "K7ABC",
        "AB1C",
    )
    assert extract_callsigns("3DA0RS, 9A1A, K7ABC/P and VK2ABC") == (
        "3DA0RS",
        "9A1A",
        "K7ABC",
        "VK2ABC",
    )


def test_resolver_handles_international_phonetics_and_accented_numbers() -> None:
    resolver = CallsignResolver()

    assert resolver.resolve("Three Delta Alpha Zero Romeo Sierra checking in") == (
        "3DA0RS checking in"
    )
    assert resolver.resolve("Nine Alpha One Alpha calling") == "9A1A calling"
    assert resolver.resolve("Kilo Mike Tree Golf Hotel Sierra") == "KM3GHS"
    assert resolver.resolve("K7ABC/P portable") == "K7ABC/P portable"


def test_resolver_collapses_multiple_doubled_phonetic_symbols() -> None:
    result = CallsignResolver().resolve_detailed(
        "Kilo Kilo Mike Mike Seven Seven Golf Golf Hotel Hotel Sierra Sierra"
    )

    assert result.text == "KM7GHS"
    assert result.corrections[0].confidence == "medium"
    assert result.corrections[0].reason == "repeated-symbol collapse"


def test_resolver_tolerates_fillers_and_one_character_phonetic_misspellings() -> None:
    resolver = CallsignResolver()

    assert resolver.resolve("Kilo uh Mike Seven Golf Hotel Siera") == "KM7GHS"


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


def test_us_shaped_fragments_must_follow_us_callsign_structure() -> None:
    assert extract_callsigns("KTW4LKT K04VAP AI4DQPK, but W3UW and AI4DQP") == (
        "W3UW",
        "AI4DQP",
    )


def test_resolver_recovers_confirmed_calls_from_run_together_decodes() -> None:
    resolver = CallsignResolver(("W3UW", "N5AQM"))

    assert resolver.resolve("W3UWUW3UW calling") == "W3UW calling"
    assert resolver.resolve("MN5AQMM in Chandler") == "N5AQM in Chandler"
