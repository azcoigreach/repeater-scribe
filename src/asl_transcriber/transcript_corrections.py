"""Operator edits to selected transcript text, separate from the raw decoder output."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from asl_transcriber.callsign_service import _get_or_create, canonical_callsign
from asl_transcriber.models import CallsignMention, Transcript, TranscriptSegment


class CorrectionConflict(ValueError):
    pass


def correct_selection(
    session: Session, transcript: Transcript, *, expected_text: str, start: int, end: int,
    callsign: str, reviewer: str,
) -> CallsignMention:
    recording = transcript.recording or transcript.job.recording
    if recording is None:
        raise CorrectionConflict("This transcript has no recording")
    if recording.current_transcript_id not in (None, transcript.id):
        raise CorrectionConflict("This transcript has been replaced. Reload before correcting it.")
    if transcript.job.status in {"pending", "processing"}:
        raise CorrectionConflict("Wait for transcription to finish before correcting text.")
    if transcript.display_text != expected_text:
        raise CorrectionConflict("The transcript changed. Reload and select the text again.")
    if not 0 <= start < end <= len(expected_text) or not expected_text[start:end].strip():
        raise ValueError("Select the words that should be a callsign.")
    if end - start > 256:
        raise ValueError("Select only the words that should be a callsign (up to 256 characters).")
    value = callsign.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]+", value) or canonical_callsign(value) != value:
        raise ValueError("Enter a callsign using letters and digits, for example KM7GHS.")
    selected = expected_text[start:end]
    replacement = expected_text[:start] + value + expected_text[end:]
    changed = session.execute(
        update(Transcript).where(
            Transcript.id == transcript.id, Transcript.display_text == expected_text
        ).values(display_text=replacement), execution_options={"synchronize_session": False},
    )
    if cast(CursorResult, changed).rowcount != 1:
        raise CorrectionConflict("The transcript changed. Reload and select the text again.")
    # Map only exact segment text. Never invent word-level audio timestamps.
    patches = []
    containing_segment = None
    containing_start = 0
    cursor = 0
    for segment in sorted(transcript.segments, key=lambda row: row.ordinal):
        position = expected_text.find(segment.display_text, cursor) if segment.display_text else -1
        if position < 0:
            continue
        cursor = position + len(segment.display_text)
        if start < cursor and end > position:
            left, right = max(start, position) - position, min(end, cursor) - position
            after = segment.display_text[:left] + (value if start >= position else '')
            after += segment.display_text[right:]
            patches.append({"ordinal": segment.ordinal, "before": segment.display_text, "after": after})
            segment.display_text = after
            if position <= start and end <= cursor:
                containing_segment = segment
                containing_start = position
    candidates = [
        mention for mention in transcript.callsign_mentions
        if mention.is_current and mention.canonical_callsign == selected.strip().upper()
        and (containing_segment is None or mention.segment_id == containing_segment.id)
    ]
    mention: CallsignMention | None
    if len(candidates) > 1:
        # Segment timestamps cannot distinguish repeated words in the same segment.
        # Associate repeated occurrences in their existing audio order.
        candidates.sort(key=lambda item: (item.start_offset or 0, item.id))
        occurrence = len(list(re.finditer(
            rf"\b{re.escape(selected.strip())}\b", expected_text[containing_start:start],
            re.IGNORECASE,
        )))
        mention = candidates[min(occurrence, len(candidates) - 1)]
    else:
        mention = candidates[0] if candidates else None
    now = datetime.now(UTC)
    station = _get_or_create(session, value)
    if mention is None:
        mention = CallsignMention(
            id=str(uuid4()), transcript_id=transcript.id, recording_id=recording.id,
            raw_observed_value=selected[:64], recognition_method="manual",
            segment_id=containing_segment.id if containing_segment else None,
            start_offset=containing_segment.start_offset if containing_segment else None,
            end_offset=containing_segment.end_offset if containing_segment else None,
            timing_precision="segment" if containing_segment else "recording",
            heard_at=(recording.started_at + timedelta(seconds=containing_segment.end_offset)
                      if recording.started_at and containing_segment else recording.started_at),
            evidence_json=json.dumps([f'Operator selected "{selected}" from the transcript']),
        )
        session.add(mention)
    mention.callsign_id = station.id
    mention.canonical_callsign = value
    mention.qrz_validation_status = station.qrz_status
    mention.review_status = "corrected"
    mention.reviewer_identity = reviewer[:255]
    mention.reviewed_at = now
    mention.is_current = True
    transcript.display_text = replacement
    transcript.recording_id = recording.id
    recording.current_transcript_id = transcript.id
    session.flush()
    correction = {
        "before": expected_text, "after": replacement, "selected_text": selected,
        "start": start, "end": end, "segments": patches,
        "mention": {key: getattr(mention, key) for key in (
            "id", "callsign_id", "canonical_callsign", "raw_observed_value",
            "start_offset", "end_offset", "timing_precision", "recognition_method",
            "confidence", "acoustic_confidence", "recognition_confidence", "evidence_json",
            "qrz_validation_status", "reviewer_identity",
        )},
        "reviewed_at": now.isoformat(),
        "heard_at": mention.heard_at.isoformat() if mention.heard_at else None,
        "review_status": "corrected", "applied": True,
    }
    transcript.text_corrections_json = json.dumps(
        json.loads(transcript.text_corrections_json or "[]") + [correction]
    )
    return mention


def replay_corrections(session: Session, transcript: Transcript) -> None:
    """Replay only exact text revisions; retain unmatched edits as history."""
    corrections = json.loads(transcript.text_corrections_json or "[]")
    if not corrections:
        return
    already_applied: set[int] = set()
    previous_text = transcript.display_text
    for index in range(len(corrections) - 1, -1, -1):
        if corrections[index]["after"] == previous_text:
            already_applied.add(index)
            previous_text = corrections[index]["before"]
    segment_rows = list(session.scalars(
        select(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id)
    ))
    for index, edit in enumerate(corrections):
        edit["applied"] = False
        if index not in already_applied:
            if transcript.display_text != edit["before"]:
                continue
            transcript.display_text = edit["after"]
        edit["applied"] = True
        for patch in edit["segments"]:
            for segment in segment_rows:
                if segment.ordinal == patch["ordinal"] and segment.display_text == patch["before"]:
                    segment.display_text = patch["after"]
        saved = edit["mention"]
        # A decoder may now recognize the corrected callsign at the same segment.
        # Keep the human-reviewed identity instead of counting both detections.
        duplicates = session.scalars(select(CallsignMention).where(
            CallsignMention.transcript_id == transcript.id,
            CallsignMention.id != saved["id"],
            CallsignMention.review_status == "detected",
            CallsignMention.is_current.is_(True),
            CallsignMention.canonical_callsign.in_(
                [saved["canonical_callsign"], saved["raw_observed_value"]]
            ),
            CallsignMention.start_offset == saved["start_offset"],
            CallsignMention.end_offset == saved["end_offset"],
        ))
        for duplicate in duplicates:
            session.delete(duplicate)
        mention = session.get(CallsignMention, saved["id"], populate_existing=True)
        if mention is None:
            mention = CallsignMention(
                id=saved["id"], transcript_id=transcript.id, recording_id=transcript.recording_id,
            )
            session.add(mention)
        for key, value in saved.items():
            setattr(mention, key, value)
        mention.is_current = True
        mention.review_status = edit.get("review_status", "corrected")
        mention.reviewed_at = datetime.fromisoformat(edit["reviewed_at"])
        mention.heard_at = datetime.fromisoformat(edit["heard_at"]) if edit["heard_at"] else None
        mention.segment_id = next((
            segment.id for segment in segment_rows
            if segment.start_offset == saved["start_offset"]
            and segment.end_offset == saved["end_offset"]
        ), None)
    transcript.text_corrections_json = json.dumps(corrections)
