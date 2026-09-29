from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from asl_transcriber import accounts, auth, cli
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
        "role": "user",
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


def test_recover_admin_uses_configured_issuer_and_reports_fresh_login(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(sys, "argv", ["asl-transcriber", "recover-admin", "Exact-Subject"])
    monkeypatch.setattr(cli.settings, "oidc_issuer_url", "https://identity.example.test/")

    def recover(issuer, subject):
        calls.append((issuer, subject))
        return "recovered-account"

    monkeypatch.setattr(accounts, "recover_admin", recover)
    cli.main()

    assert calls == [("https://identity.example.test", "Exact-Subject")]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "account_id": "recovered-account", "role": "admin", "sign_in_required": True,
    }
    assert captured.err == ""


@pytest.mark.parametrize("issuer,subject", [("", "subject"), ("https://id.test", "")])
def test_recover_admin_invalid_configuration_or_subject_is_cli_error(
    monkeypatch, capsys, issuer, subject,
):
    monkeypatch.setattr(sys, "argv", ["asl-transcriber", "recover-admin", subject])
    monkeypatch.setattr(cli.settings, "oidc_issuer_url", issuer)

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Recovery requires a configured HTTPS issuer" in captured.err
