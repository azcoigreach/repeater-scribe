from __future__ import annotations

import re
from dataclasses import dataclass, field

_PHONETIC_SYMBOLS = {
    "alpha": "A",
    "alfa": "A",
    "bravo": "B",
    "charlie": "C",
    "delta": "D",
    "echo": "E",
    "foxtrot": "F",
    "golf": "G",
    "hotel": "H",
    "honolulu": "H",
    "india": "I",
    "juliett": "J",
    "juliet": "J",
    "kilo": "K",
    "kilowatt": "K",
    "lima": "L",
    "mike": "M",
    "november": "N",
    "oscar": "O",
    "papa": "P",
    "quebec": "Q",
    "romeo": "R",
    "sierra": "S",
    "sugar": "S",
    "tango": "T",
    "uniform": "U",
    "victor": "V",
    "whiskey": "W",
    "xray": "X",
    "x-ray": "X",
    "yankee": "Y",
    "zulu": "Z",
    "zed": "Z",
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "tree": "3",
    "four": "4",
    "five": "5",
    "fife": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "niner": "9",
}
_NATO = {
    "A": "Alpha",
    "B": "Bravo",
    "C": "Charlie",
    "D": "Delta",
    "E": "Echo",
    "F": "Foxtrot",
    "G": "Golf",
    "H": "Hotel",
    "I": "India",
    "J": "Juliett",
    "K": "Kilo",
    "L": "Lima",
    "M": "Mike",
    "N": "November",
    "O": "Oscar",
    "P": "Papa",
    "Q": "Quebec",
    "R": "Romeo",
    "S": "Sierra",
    "T": "Tango",
    "U": "Uniform",
    "V": "Victor",
    "W": "Whiskey",
    "X": "X-ray",
    "Y": "Yankee",
    "Z": "Zulu",
    "0": "Zero",
    "1": "One",
    "2": "Two",
    "3": "Three",
    "4": "Four",
    "5": "Five",
    "6": "Six",
    "7": "Seven",
    "8": "Eight",
    "9": "Nine",
}
_CALLSIGN = re.compile(r"^(?P<prefix>[A-Z0-9]{1,3})\d[A-Z]{1,4}$")
_CALLSIGN_IN_TEXT = re.compile(
    r"(?<![A-Z0-9])([A-Z0-9]{1,3}\d[A-Z]{1,4}(?:/[A-Z0-9]{1,4})?)(?![A-Z0-9])",
    re.IGNORECASE,
)
_US_CALLSIGN = re.compile(r"^(?:[KNW][A-Z]?|A[A-L])\d[A-Z]{1,3}$")
_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)?|\d")
_DIGIT_LIKE = {"I": "1", "L": "1", "O": "0"}
_LOW_COST_GROUPS = (
    frozenset("I1L"),
    frozenset("O0"),
    frozenset("S5"),
    frozenset("BDGPTVZ"),
    frozenset("MN"),
    frozenset("FSX"),
    frozenset("AK"),
)
_FUZZY_PHONETICS = {
    spoken.casefold(): symbol for symbol, spoken in _NATO.items() if symbol.isalpha()
}
_FILLER_WORDS = {"er", "uh", "um"}


def normalize_callsigns(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        choices = value.upper().split("/")
        candidate = next(
            (
                compact
                for choice in choices
                if (compact := re.sub(r"[^A-Z0-9]", "", choice))
                and _is_callsign(compact)
            ),
            "",
        )
        if candidate and candidate not in seen:
            normalized.append(candidate)
            seen.add(candidate)
    return tuple(normalized)


def _is_callsign(candidate: str) -> bool:
    match = _CALLSIGN.fullmatch(candidate)
    return match is not None and any(symbol.isalpha() for symbol in match.group("prefix"))


def extract_callsigns(text: str) -> tuple[str, ...]:
    """Return unique, normalized callsigns already present in transcript text."""
    return normalize_callsigns([match.group(1) for match in _CALLSIGN_IN_TEXT.finditer(text)])


def callsign_hotwords(callsigns: list[str] | tuple[str, ...], extra: str | None = None) -> str:
    """Build compact written and spoken hints for the locally relevant callsigns."""
    hints: list[str] = []
    for callsign in normalize_callsigns(callsigns):
        hints.append(callsign)
        hints.append(" ".join(_NATO[symbol] for symbol in callsign))
    if extra:
        hints.append(extra.strip())
    return ", ".join(hint for hint in hints if hint)


@dataclass(frozen=True)
class CallsignCorrection:
    original: str
    corrected: str
    confidence: str
    reason: str


@dataclass(frozen=True)
class CallsignResolution:
    text: str
    corrections: tuple[CallsignCorrection, ...] = ()


@dataclass(frozen=True)
class _CandidateMatch:
    callsign: str
    tier: int
    score: float
    confidence: str
    reason: str


@dataclass(frozen=True)
class CallsignResolver:
    """Normalize callsign-shaped text using radio grammar and ranked local candidates."""

    known_callsigns: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "known_callsigns", normalize_callsigns(self.known_callsigns))

    def resolve(self, text: str) -> str:
        return self.resolve_detailed(text).text

    def resolve_detailed(self, text: str) -> CallsignResolution:
        if not text:
            return CallsignResolution(text)

        words = list(_WORD.finditer(text))
        replacements: list[tuple[int, int, CallsignCorrection]] = []
        index = 0
        while index < len(words):
            best: tuple[tuple[int, float, int], int, _CandidateMatch] | None = None
            symbols: list[str] = []
            for end in range(index, min(index + 16, len(words))):
                if end > index:
                    separator = text[words[end - 1].end() : words[end].start()]
                    if any(character in ".!?;/\n'" for character in separator):
                        break
                raw_word = words[end].group(0)
                piece = self._piece_symbols(raw_word, "".join(symbols))
                if piece is None:
                    break
                symbols.extend(piece)
                observed = "".join(symbols)
                if len(observed) > 16:
                    break
                match = self._match(observed)
                if match is None:
                    continue
                quality = (match.tier, match.score, -len(observed))
                if best is None or quality < best[0]:
                    best = (quality, end, match)
            if best is None:
                index += 1
                continue
            _, end, match = best
            start_offset = words[index].start()
            end_offset = words[end].end()
            original = text[start_offset:end_offset]
            if original != match.callsign:
                replacements.append(
                    (
                        start_offset,
                        end_offset,
                        CallsignCorrection(
                            original=original,
                            corrected=match.callsign,
                            confidence=match.confidence,
                            reason=match.reason,
                        ),
                    )
                )
            index = end + 1

        for start, end, correction in reversed(replacements):
            text = f"{text[:start]}{correction.corrected}{text[end:]}"
        return CallsignResolution(text=text, corrections=tuple(item[2] for item in replacements))

    def _match(self, observed: str) -> _CandidateMatch | None:
        known_match = self._known_match(observed)
        if known_match is not None:
            return known_match

        structural = self._structural_callsign(observed)
        if structural is not None:
            callsign, changed_digit = structural
            return _CandidateMatch(
                callsign=callsign,
                tier=2 if changed_digit else 3,
                score=0.0,
                confidence="medium" if changed_digit else "high",
                reason="numeric-slot normalization" if changed_digit else "callsign formatting",
            )

        collapsed = self._collapse_repeated_symbols(observed)
        if len(observed) - len(collapsed) < 2:
            return None
        collapsed_known = self._known_match(collapsed)
        if collapsed_known is not None:
            return _CandidateMatch(
                callsign=collapsed_known.callsign,
                tier=1,
                score=collapsed_known.score,
                confidence="medium",
                reason="repeated-symbol collapse to local candidate",
            )
        collapsed_structural = self._structural_callsign(collapsed)
        if collapsed_structural is None:
            return None
        callsign, _ = collapsed_structural
        return _CandidateMatch(
            callsign=callsign,
            tier=2,
            score=0.0,
            confidence="medium",
            reason="repeated-symbol collapse",
        )

    def _known_match(self, observed: str) -> _CandidateMatch | None:
        if observed in self.known_callsigns:
            return _CandidateMatch(observed, 0, 0.0, "high", "exact local candidate")
        if not self.known_callsigns or len(observed) < 4:
            return None

        ranked = sorted(
            (self._weighted_distance(observed, candidate), index, candidate)
            for index, candidate in enumerate(self.known_callsigns)
            if abs(len(candidate) - len(observed)) <= 1
        )
        if not ranked:
            return None
        best_distance, best_index, best_candidate = ranked[0]
        threshold = min(1.4, max(1.0, len(best_candidate) * 0.24))
        if best_distance > threshold:
            return None
        if len(ranked) > 1:
            competing_distance, competing_index, _ = ranked[1]
            best_ranked_score = best_distance + self._candidate_prior_penalty(best_index)
            competing_ranked_score = competing_distance + self._candidate_prior_penalty(
                competing_index
            )
            if competing_ranked_score - best_ranked_score < 0.35:
                return None
        return _CandidateMatch(
            callsign=best_candidate,
            tier=1,
            score=best_distance,
            confidence="high" if best_distance <= 0.8 else "medium",
            reason=f"local candidate weighted distance {best_distance:.2f}",
        )

    @staticmethod
    def _candidate_prior_penalty(index: int) -> float:
        """Use ranked local relevance only to break otherwise ambiguous matches."""
        return min(index, 100) * 0.004

    @staticmethod
    def _structural_callsign(observed: str) -> tuple[str, bool] | None:
        if _is_callsign(observed):
            return observed, False
        variants: set[str] = set()
        for digit_index in (1, 2):
            if digit_index >= len(observed) - 1:
                continue
            digit = _DIGIT_LIKE.get(observed[digit_index])
            if digit is None:
                continue
            candidate = f"{observed[:digit_index]}{digit}{observed[digit_index + 1:]}"
            if _US_CALLSIGN.fullmatch(candidate):
                variants.add(candidate)
        if len(variants) == 1:
            return variants.pop(), True
        return None

    @staticmethod
    def _collapse_repeated_symbols(observed: str) -> str:
        return "".join(
            symbol for index, symbol in enumerate(observed) if index == 0 or symbol != observed[index - 1]
        )

    @classmethod
    def _weighted_distance(cls, left: str, right: str) -> float:
        previous = [float(index) for index in range(len(right) + 1)]
        for left_index, left_symbol in enumerate(left, start=1):
            current = [float(left_index)]
            for right_index, right_symbol in enumerate(right, start=1):
                substitution = previous[right_index - 1] + cls._substitution_cost(
                    left_symbol, right_symbol
                )
                insertion = current[right_index - 1] + 0.85
                deletion = previous[right_index] + 0.85
                current.append(min(substitution, insertion, deletion))
            previous = current
        return previous[-1]

    @staticmethod
    def _substitution_cost(left: str, right: str) -> float:
        if left == right:
            return 0.0
        for group in _LOW_COST_GROUPS:
            if left in group and right in group:
                if group == frozenset("I1L") or group == frozenset("O0"):
                    return 0.2
                if group == frozenset("S5"):
                    return 0.3
                return 0.7
        return 1.0

    @classmethod
    def _piece_symbols(cls, word: str, current: str) -> str | None:
        if current and word.casefold() in _FILLER_WORDS:
            return ""
        symbol = cls._symbol(word)
        if symbol is not None:
            return symbol
        digit_index = next((index for index, char in enumerate(current) if char.isdigit()), None)
        suffix_length = len(current) - digit_index - 1 if digit_index is not None else -1
        if word.isalpha() and word.isupper() and len(word) <= 8:
            if digit_index is not None and suffix_length > 0:
                return None
            return word
        if (
            digit_index is not None
            and suffix_length == 0
            and word.isalpha()
            and word[:1].isupper()
            and len(word) <= 4
        ):
            return word.upper()
        return None

    @classmethod
    def _symbol(cls, word: str) -> str | None:
        normalized = word.casefold()
        if normalized in _PHONETIC_SYMBOLS:
            return _PHONETIC_SYMBOLS[normalized]
        if len(word) == 1 and word.isalnum() and (word.isdigit() or word.isupper()):
            return word.upper()
        if normalized.isalpha() and len(normalized) >= 4:
            ranked = sorted(
                (cls._word_distance(normalized, spoken), symbol)
                for spoken, symbol in _FUZZY_PHONETICS.items()
            )
            if ranked and ranked[0][0] == 1 and (len(ranked) == 1 or ranked[1][0] > 1):
                return ranked[0][1]
        return None

    @staticmethod
    def _word_distance(left: str, right: str) -> int:
        previous = list(range(len(right) + 1))
        for left_index, left_symbol in enumerate(left, start=1):
            current = [left_index]
            for right_index, right_symbol in enumerate(right, start=1):
                current.append(
                    min(
                        previous[right_index] + 1,
                        current[right_index - 1] + 1,
                        previous[right_index - 1] + (left_symbol != right_symbol),
                    )
                )
            previous = current
        return previous[-1]
