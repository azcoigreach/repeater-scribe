from __future__ import annotations

import argparse
import json
from pathlib import Path

from asl_transcriber.audio.probe import probe_audio
from asl_transcriber.config import settings
from asl_transcriber.main import app, build_local_transcription_engine
from asl_transcriber.runtime import ArchiveRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="asl-transcriber")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("serve", help="Start the web API")
    subparsers.add_parser("worker", help="Run the processing worker")
    subparsers.add_parser("scan", help="Perform an archive scan")
    subparsers.add_parser("config", help="Print the effective configuration")
    subparsers.add_parser("db", help="Database-related commands")
    subparsers.add_parser("health", help="Print health status")
    benchmark = subparsers.add_parser(
        "benchmark", help="Benchmark the configured local model on archive recordings"
    )
    benchmark.add_argument("audio", nargs="+", type=Path)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "scan":
        runtime = ArchiveRuntime(settings.archive_path_list)
        jobs = runtime.scan_once()
        print(
            json.dumps(
                {
                    "created": len(jobs),
                    "total": len(runtime.jobs()),
                    "archive_paths": settings.archive_path_list,
                }
            )
        )
        return

    if args.command == "benchmark":
        engine = build_local_transcription_engine()
        items: list[dict[str, object]] = []
        for audio_path in args.audio:
            probe = probe_audio(audio_path)
            result = engine.transcribe(str(audio_path))
            processing_seconds = result.processing_time_seconds or 0.0
            items.append(
                {
                    "path": str(audio_path),
                    "audio_seconds": round(probe.duration_seconds, 3),
                    "processing_seconds": round(processing_seconds, 3),
                    "real_time_factor": (
                        round(processing_seconds / probe.duration_seconds, 3)
                        if probe.duration_seconds
                        else None
                    ),
                    "model": result.model_name,
                    "device": result.options.get("device"),
                    "compute_type": result.options.get("compute_type"),
                    "raw_text": result.raw_text,
                    "display_text": result.display_text,
                }
            )
        print(json.dumps({"items": items}, indent=2))
        return

    if args.command in {"serve", "config", "health"}:
        if args.command == "health":
            print("ok")
            return
        if args.command == "config":
            print(app.title)
            return
        print("serve command is handled by uvicorn in deployment")
        return

    print(f"Command {args.command} is not implemented in this phase.")


if __name__ == "__main__":
    main()
