from __future__ import annotations

import argparse
import json
import os
import sqlite3
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
    backup = subparsers.add_parser("backup-db", help="Create an online SQLite backup")
    backup.add_argument("destination", type=Path)
    backup.add_argument("--force", action="store_true", help="Replace an existing backup")
    verify_backup = subparsers.add_parser("verify-db", help="Verify a SQLite backup")
    verify_backup.add_argument("database", type=Path)
    create_token = subparsers.add_parser(
        "create-api-token", help="Create a named API token and print its secret once"
    )
    create_token.add_argument("name")
    create_token.add_argument("--role", choices=("viewer", "operator", "admin"), default="operator")
    revoke_token = subparsers.add_parser("revoke-api-token", help="Revoke a named API token")
    revoke_token.add_argument("name")
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

    if args.command == "create-api-token":
        from asl_transcriber.auth import create_api_token, revoke_api_token

        if not args.name.strip() or len(args.name) > 128:
            parser.error("token name must contain 1 to 128 characters")
        try:
            terminal_fd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY)
        except OSError:
            parser.error("create-api-token requires a controlling terminal; do not use exec -T")
        name = args.name.strip()
        try:
            token = create_api_token(name, args.role)
            try:
                pending = (json.dumps({"name": name, "role": args.role, "token": token}) + "\n").encode(
                    "utf-8"
                )
                while pending:
                    written = os.write(terminal_fd, pending)
                    if written <= 0:
                        raise OSError("terminal write made no progress")
                    pending = pending[written:]
            except OSError:
                revoke_api_token(name)
                parser.error("could not deliver the token to the controlling terminal")
        finally:
            os.close(terminal_fd)
        return

    if args.command == "revoke-api-token":
        from asl_transcriber.auth import revoke_api_token

        revoked = revoke_api_token(args.name.strip())
        print(json.dumps({"revoked": revoked}))
        return

    if args.command == "backup-db":
        from asl_transcriber.backup import backup_database

        try:
            destination = backup_database(
                settings.database_url, args.destination, force=args.force
            )
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps({"backup": str(destination), "verified": True}))
        return

    if args.command == "verify-db":
        from asl_transcriber.backup import verify_database

        try:
            tables = verify_database(args.database)
        except (ValueError, sqlite3.DatabaseError) as error:
            parser.error(str(error))
        print(json.dumps({"database": str(args.database.resolve()), "tables": tables}))
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
