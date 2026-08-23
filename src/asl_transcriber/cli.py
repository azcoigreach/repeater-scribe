from __future__ import annotations

import argparse
import json

from asl_transcriber.config import settings
from asl_transcriber.main import app
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
