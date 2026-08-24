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
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
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
_CALLSIGN = re.compile(r"^[A-Z]{1,2}\d[A-Z]{1,4}$")
_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)?|\d")


def normalize_callsigns(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized = {re.sub(r"[^A-Z0-9]", "", value.upper()) for value in values}
    return tuple(sorted(value for value in normalized if _CALLSIGN.fullmatch(value)))


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
class CallsignResolver:
    """Correct phonetic callsigns only when they match a configured local candidate."""

    known_callsigns: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "known_callsigns", normalize_callsigns(self.known_callsigns))

    def resolve(self, text: str) -> str:
        if not text or not self.known_callsigns:
            return text

        known = set(self.known_callsigns)
        words = list(_WORD.finditer(text))
        replacements: list[tuple[int, int, str]] = []
        index = 0
        while index < len(words):
            best: tuple[int, str] | None = None
            symbols: list[str] = []
            for end in range(index, min(index + 8, len(words))):
                raw_word = words[end].group(0)
                symbol = self._symbol(raw_word)
                if symbol is None:
                    break
                symbols.append(symbol)
                candidate = "".join(symbols)
                if candidate in known:
                    best = (end, candidate)
            if best is None:
                index += 1
                continue
            end, candidate = best
            replacements.append((words[index].start(), words[end].end(), candidate))
            index = end + 1

        for start, end, replacement in reversed(replacements):
            text = f"{text[:start]}{replacement}{text[end:]}"
        return text

    @staticmethod
    def _symbol(word: str) -> str | None:
        normalized = word.casefold()
        if normalized in _PHONETIC_SYMBOLS:
            return _PHONETIC_SYMBOLS[normalized]
        if len(word) == 1 and word.isalnum():
            return word.upper()
        return None
