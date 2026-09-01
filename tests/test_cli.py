from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from asl_transcriber import auth, cli
from asl_transcriber.cli import build_parser


def test_benchmark_command_accepts_multiple_local_audio_files() -> None:
    args = build_parser().parse_args(["benchmark", "one.wav", "two.wav"])

    assert args.command == "benchmark"
    assert args.audio == [Path("one.wav"), Path("two.wav")]


def test_create_api_token_delivers_secret_only_to_controlling_terminal(monkeypatch) -> None:
    writes: list[bytes] = []
    closed: list[int] = []
    monkeypatch.setattr(sys, "argv", ["asl-transcriber", "create-api-token", "automation"])
    monkeypatch.setattr(cli.os, "open", lambda *_args: 7)
    monkeypatch.setattr(cli.os, "write", lambda _fd, data: writes.append(data) or len(data))
    monkeypatch.setattr(cli.os, "close", closed.append)
    monkeypatch.setattr(auth, "create_api_token", lambda _name, _role: "one-time-secret")

    cli.main()

    assert json.loads(writes[0]) == {
        "name": "automation",
        "role": "operator",
        "token": "one-time-secret",
    }
    assert closed == [7]


def test_create_api_token_fails_before_creation_without_a_terminal(monkeypatch) -> None:
    created = False

    def fail_open(*_args):
        raise OSError("no terminal")

    def create_token(_name, _role):
        nonlocal created
        created = True
        return "unreachable"

    monkeypatch.setattr(sys, "argv", ["asl-transcriber", "create-api-token", "automation"])
    monkeypatch.setattr(cli.os, "open", fail_open)
    monkeypatch.setattr(auth, "create_api_token", create_token)

    with pytest.raises(SystemExit):
        cli.main()

    assert not created
