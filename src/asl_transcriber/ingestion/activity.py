from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class ActivityLogEvent:
    timestamp: datetime
    node_id: int | None
    event_type: str
    details: str | None = None
    raw: str = ""


class ActivityLogParser:
    _line_re = re.compile(
        r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) NODE (?P<node>\d+): (?P<message>.+)$"
    )

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._text: str | None = None

    @classmethod
    def from_text(cls, text: str) -> ActivityLogParser:
        parser = cls(None)
        parser._text = text
        return parser

    def parse(self) -> list[ActivityLogEvent]:
        if self._text is not None:
            lines = self._text.splitlines()
        else:
            if self.path is None or not self.path.exists():
                return []
            lines = self.path.read_text(encoding="utf-8").splitlines()

        events: list[ActivityLogEvent] = []
        for line in lines:
            if not line.strip():
                continue
            match = self._line_re.match(line.strip())
            if match is not None:
                timestamp = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                node_id: int | None = int(match.group("node"))
                message = match.group("message")
                event_type, details = self._split_event(message)
            else:
                daily = next(csv.reader([line.strip()]), [])
                if len(daily) < 3 or not re.fullmatch(r"\d{16}", daily[0]):
                    continue
                timestamp = datetime.strptime(daily[0][:14], "%Y%m%d%H%M%S").replace(
                    tzinfo=UTC, microsecond=int(daily[0][14:]) * 10000
                )
                event_type = daily[1].strip()
                node_id = int(daily[2]) if daily[2].isdigit() and event_type == "TELEMETRY" else None
                details = ",".join(daily[2:])
            events.append(
                ActivityLogEvent(
                    timestamp=timestamp,
                    node_id=node_id,
                    event_type=event_type,
                    details=details,
                    raw=line.strip(),
                )
            )
        return events

    @staticmethod
    def _split_event(message: str) -> tuple[str, str | None]:
        cleaned = message.strip()
        if ": " in cleaned:
            event_type, remainder = cleaned.split(": ", 1)
            return event_type.strip(), remainder.strip() or None

        match = re.match(r"^(?P<event>.+?)(?: \((?P<details>.*)\))?$", cleaned)
        if match is None:
            return cleaned, None

        event_type = match.group("event").strip()
        details = match.group("details")
        return event_type, details.strip() if details else None

    def correlate_recording(
        self,
        recording_time: datetime,
        tolerance_seconds: int = 30,
        node_id: int | None = None,
    ) -> ActivityLogEvent | None:
        events = self.parse()
        if not events:
            return None

        if node_id is not None:
            events = [event for event in events if event.node_id == node_id]

        best_match: ActivityLogEvent | None = None
        best_delta: float | None = None

        for event in events:
            delta = abs((event.timestamp - recording_time).total_seconds())
            if delta > tolerance_seconds:
                continue
            if best_delta is None or delta < best_delta:
                best_match = event
                best_delta = delta

        return best_match
