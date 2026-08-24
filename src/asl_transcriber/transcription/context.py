from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic

from sqlalchemy import select
from sqlalchemy.orm import Session

from asl_transcriber.models import (
    Favorite,
    FavoriteStatsSnapshot,
    RemoteNodeStat,
    TopologyNodeSnapshot,
)
from asl_transcriber.transcription.callsigns import normalize_callsigns


@dataclass
class DatabaseCallsignProvider:
    """Return locally relevant callsigns, ordered from strongest to weakest prior."""

    session_factory: Callable[[], Session]
    configured_callsigns: tuple[str, ...] = ()
    cache_seconds: float = 30.0
    max_candidates: int = 250
    clock: Callable[[], float] = monotonic
    _cached: tuple[str, ...] = field(default_factory=tuple, init=False, repr=False)
    _cached_at: float = field(default=float("-inf"), init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def __call__(self) -> tuple[str, ...]:
        now = self.clock()
        with self._lock:
            if self._cached and now - self._cached_at < self.cache_seconds:
                return self._cached
            self._cached = self._load()
            self._cached_at = now
            return self._cached

    def _load(self) -> tuple[str, ...]:
        priorities: dict[str, int] = {}

        def add(value: object, priority: int) -> None:
            if not isinstance(value, str):
                return
            normalized = normalize_callsigns((value,))
            if normalized:
                callsign = normalized[0]
                priorities[callsign] = max(priority, priorities.get(callsign, 0))

        for callsign in self.configured_callsigns:
            add(callsign, 100)

        with self.session_factory() as session:
            for favorite in session.scalars(select(Favorite)).all():
                add(favorite.callsign_override, 95)
                add(favorite.target_identifier, 80)

            for snapshot in session.scalars(select(FavoriteStatsSnapshot)).all():
                add(snapshot.callsign, 95 if snapshot.keyed else 88 if snapshot.active else 70)

            for remote_stat in session.scalars(select(RemoteNodeStat)).all():
                add(remote_stat.remote_identifier, 93 if remote_stat.active_keyed_at else 72)

            for topology_node in session.scalars(select(TopologyNodeSnapshot)).all():
                priority = 96 if topology_node.keyed else 90 if topology_node.active else 68
                metadata = self._json_object(topology_node.metadata_json)
                add(metadata.get("callsign"), priority)
                for neighbor in self._json_list(topology_node.neighbors_json):
                    if isinstance(neighbor, dict):
                        neighbor_priority = 87 if neighbor.get("active") else 65
                        add(neighbor.get("callsign"), neighbor_priority)

        ranked = sorted(priorities, key=lambda callsign: (-priorities[callsign], callsign))
        return tuple(ranked[: self.max_candidates])

    @staticmethod
    def _json_object(value: str) -> dict[str, object]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_list(value: str) -> list[object]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
