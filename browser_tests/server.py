"""Deterministic, isolated acceptance server. Never run against a production DB."""

import json
import os
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_DNS, uuid5

import uvicorn

from asl_transcriber import main
from asl_transcriber.auth import token_digest
from asl_transcriber.callsign_service import persist_transcript_details
from asl_transcriber.config import settings
from asl_transcriber.database import SessionLocal
from asl_transcriber.models import (
    AuthSession,
    Callsign,
    CallsignMention,
    IngestionJob,
    Recording,
    Transcript,
)
from asl_transcriber.qrz import QrzCallsign
from asl_transcriber.transcription.base import TranscriptCallsignMention, TranscriptSegment

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
HOSTILE = '<img src=x onerror="window.hostile=true"> & <script>window.hostile=true</script>'
root = Path(settings.archive_path_list[0])
root.mkdir(parents=True, exist_ok=True)
with wave.open(str(root / "sample.wav"), "wb") as audio:
    audio.setnchannels(1)
    audio.setsampwidth(2)
    audio.setframerate(8000)
    audio.writeframes(b"\0\0" * 8000 * 12)
ids = {}
with SessionLocal() as db:
    for index in range(56):
        value = "KM7GHS" if index == 0 else f"K1{chr(65 + index // 26)}{chr(65 + index % 26)}"
        identifier = str(uuid5(NAMESPACE_DNS, f"browser-recording-{index}"))
        ids[value] = identifier
        recording = Recording(
            id=identifier,
            archive_root=str(root),
            source_path="sample.wav" if index == 0 else f"missing-{index}.wav",
            started_at=NOW - timedelta(days=index),
            status="completed",
            audio_status="available" if index == 0 else "missing",
        )
        db.add(recording)
        db.flush()
        db.add(
            IngestionJob(
                id=identifier,
                recording_id=identifier,
                source_path=recording.source_path,
                archive_root=str(root),
                status="completed",
            )
        )
        db.flush()
        transcript = Transcript(
            id=identifier,
            job_id=identifier,
            recording_id=identifier,
            raw_text=f"Raw KM7GHS {HOSTILE}",
            display_text=f"{value} {HOSTILE}" if index == 0 else f"Legacy full text {value}",
        )
        db.add(transcript)
        count = 55 if index == 0 else 1
        persist_transcript_details(
            db,
            transcript,
            recording,
            SimpleNamespace(
                segments=[
                    TranscriptSegment(
                        2, 4, f"KM7GHS {HOSTILE}", raw_text="raw observed", confidence=-0.25
                    )
                ]
                if index == 0
                else [],
                callsign_mentions=[
                    TranscriptCallsignMention(
                        value,
                        2,
                        4,
                        confidence=0.8,
                        acoustic_confidence=0.7,
                        recognition_confidence=0.9,
                        raw_observed_value="K M 7 G H S" if index == 0 else value,
                        recognition_method="phonetic",
                        evidence=(f"Saved evidence {HOSTILE}",),
                    )
                    for _ in range(count)
                ],
            ),
        )
    db.flush()
    for call in db.query(Callsign):
        call.qrz_status = "found"
        call.qrz_display_name = f"Operator {HOSTILE}"
        call.qrz_location = "Phoenix, Arizona"
        call.qrz_image_url = "javascript:window.hostile=true"
        call.qrz_profile_url = "data:text/html,<script>window.hostile=true</script>"
        call.qrz_cache_expires_at = datetime.now(UTC) + timedelta(days=365)
    # A separate station demonstrates unavailable confidence and missing media.
    for mention in db.query(CallsignMention).filter(CallsignMention.canonical_callsign == "K1AB"):
        mention.confidence = None
        mention.acoustic_confidence = None
        mention.recognition_confidence = None
    for role in ("operator", "viewer"):
        db.add(
            AuthSession(
                token_hash=token_digest(f"browser-{role}"),
                subject=f"browser-{role}",
                identity=f"{role}@example.test",
                role=role,
                csrf_token=f"csrf-{role}",
                created_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=2),
            )
        )
    db.commit()


class StubQrz:
    def lookup(self, value):
        return QrzCallsign(
            value, name=f"Refreshed {HOSTILE}", location="Phoenix, Arizona", status="found"
        )


main.current_qrz_client = lambda: StubQrz()
main.runtime._restore_state()
Path(os.environ["BROWSER_IDS"]).write_text(json.dumps(ids))
uvicorn.run(
    main.app,
    host="127.0.0.1",
    port=int(os.environ["BROWSER_PORT"]),
    ssl_keyfile=os.environ["BROWSER_KEY"],
    ssl_certfile=os.environ["BROWSER_CERT"],
    log_level="warning",
)
