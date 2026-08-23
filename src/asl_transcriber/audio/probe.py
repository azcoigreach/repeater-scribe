from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioProbeResult:
    duration_seconds: float
    codec: str
    sample_rate: int
    channels: int
    file_size: int
    path: str


def probe_audio(path: str | Path) -> AudioProbeResult:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Audio file not found: {source}")

    ffprobe = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=codec_name,sample_rate,channels",
        "-of",
        "json",
        str(source),
    ]

    completed = subprocess.run(ffprobe, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {source}: {completed.stderr.strip()}")

    import json

    data = json.loads(completed.stdout)
    format_info = data.get("format", {})
    streams = data.get("streams", [])
    stream = streams[0] if streams else {}

    duration = float(format_info.get("duration") or 0.0)
    codec = stream.get("codec_name") or "unknown"
    sample_rate = int(stream.get("sample_rate") or 0)
    channels = int(stream.get("channels") or 0)
    file_size = int(format_info.get("size") or source.stat().st_size)

    return AudioProbeResult(
        duration_seconds=duration,
        codec=codec,
        sample_rate=sample_rate,
        channels=channels,
        file_size=file_size,
        path=str(source),
    )
