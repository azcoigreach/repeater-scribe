from __future__ import annotations

from pathlib import Path

from asl_transcriber.cli import build_parser


def test_benchmark_command_accepts_multiple_local_audio_files() -> None:
    args = build_parser().parse_args(["benchmark", "one.wav", "two.wav"])

    assert args.command == "benchmark"
    assert args.audio == [Path("one.wav"), Path("two.wav")]
