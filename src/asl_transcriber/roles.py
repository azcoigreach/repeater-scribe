"""Canonical authority shared by accounts, sessions, and machine credentials."""

from typing import Literal

Role = Literal["viewer", "user", "admin"]
ROLE_RANK = {"viewer": 1, "user": 2, "admin": 3}


def normalize_role(value: str) -> Role:
    if value == "operator":  # Released configuration and CLI compatibility.
        return "user"
    if value in ROLE_RANK:
        return value  # type: ignore[return-value]
    raise ValueError("Role must be viewer, user, or admin")
