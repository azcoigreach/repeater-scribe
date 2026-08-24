from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from asl_transcriber.models import Favorite, FavoriteStatsSnapshot


class AllStarStatsError(RuntimeError):
    pass


def is_public_node(identifier: str) -> bool:
    return identifier.isdigit() and 2_000 <= int(identifier) < 2_000_000


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _timestamp(value: object) -> datetime | None:
    seconds = _integer(value, -1)
    if seconds < 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _mode_map(nodes: object) -> dict[str, str]:
    if not isinstance(nodes, str):
        return {}
    result: dict[str, str] = {}
    modes = {"T": "transceive", "R": "receive", "C": "connecting"}
    for raw_entry in nodes.split(","):
        entry = raw_entry.strip()
        if not entry:
            continue
        mode = modes.get(entry[0].upper())
        identifier = entry[1:] if mode else entry
        if identifier:
            result[identifier] = mode or "linked"
    return result


def _number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _directory_metadata(item: dict[str, Any], identifier: str) -> dict[str, object]:
    raw_server = item.get("server")
    server: dict[str, Any] = raw_server if isinstance(raw_server, dict) else {}
    found = bool(item.get("Node_ID") or item.get("callsign") or server)
    callsign = _text(item.get("callsign"))
    if callsign is None and not identifier.isdigit():
        callsign = identifier
    return {
        "identifier": identifier,
        "callsign": callsign,
        "frequency": _text(item.get("node_frequency")),
        "tone": _text(item.get("node_tone")),
        "location": _text(server.get("Location")),
        "site_name": _text(server.get("SiteName")),
        "affiliation": _text(server.get("Affiliation")),
        "latitude": _number(server.get("Latitude")),
        "longitude": _number(server.get("Logitude")),
        "directory_status": "found" if found else "not_found",
        "active": item.get("Status") == "Active",
    }


def _topology(data: dict[str, Any]) -> list[dict[str, object]]:
    raw_links = data.get("links")
    links: list[Any] = raw_links if isinstance(raw_links, list) else []
    raw_metadata = data.get("linkedNodes")
    metadata_items: list[Any] = raw_metadata if isinstance(raw_metadata, list) else []
    metadata = {
        str(item.get("name")): item
        for item in metadata_items
        if isinstance(item, dict) and item.get("name") is not None
    }
    modes = _mode_map(data.get("nodes"))
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_identifier in links:
        identifier = str(raw_identifier).strip()
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        item = metadata.get(identifier, {})
        result.append(
            {**_directory_metadata(item, identifier), "mode": modes.get(identifier, "linked")}
        )
    return result


def parse_allstar_stats(payload: dict[str, Any], *, fetched_at: datetime | None = None) -> dict[str, object]:
    fetched = fetched_at or datetime.now(UTC)
    stats: Any = payload.get("stats")
    node: Any = payload.get("node")
    if not isinstance(stats, dict) or not isinstance(stats.get("data"), dict):
        raise AllStarStatsError("AllStar response does not contain node statistics")
    data: dict[str, Any] = stats["data"]
    if not isinstance(node, dict):
        user_node = stats.get("user_node")
        node = user_node if isinstance(user_node, dict) else {}
    identifier = str(stats.get("node") or node.get("name") or "").strip()
    if not is_public_node(identifier):
        raise AllStarStatsError("AllStar response does not identify a public node")
    topology = _topology(data)
    root_metadata = _directory_metadata(node, identifier)
    root_metadata["app_rpt_version"] = _text(data.get("apprptvers"))
    return {
        "remote_identifier": identifier,
        "callsign": _text(node.get("callsign")),
        "description": _text(node.get("node_frequency")),
        "location": root_metadata["location"],
        "active": node.get("Status") == "Active",
        "keyed": bool(data.get("keyed")),
        "total_keyups": max(0, _integer(data.get("totalkeyups"))),
        "total_tx_seconds": max(0, _integer(data.get("totaltxtime"))),
        "total_kerchunks": max(0, _integer(data.get("totalkerchunks"))),
        "uptime_seconds": max(0, _integer(data.get("apprptuptime"))),
        "link_count": len(topology),
        "topology_json": json.dumps(
            {"root": root_metadata, "links": topology}, separators=(",", ":")
        ),
        "source_reported_at": _timestamp(data.get("time")),
        "fetched_at": fetched,
    }


def fetch_allstar_stats(
    identifier: str,
    *,
    base_url: str = "http://stats.allstarlink.org/api/stats",
    timeout_seconds: float = 10.0,
) -> dict[str, object]:
    if not is_public_node(identifier):
        raise AllStarStatsError(f"{identifier} is not a public AllStar node number")
    request = Request(
        f"{base_url.rstrip('/')}/{identifier}",
        headers={"Accept": "application/json", "User-Agent": "repeater-scribe/0.3"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(2_000_001)
    except Exception as error:
        raise AllStarStatsError(f"AllStar stats request failed: {error}") from error
    if len(raw) > 2_000_000:
        raise AllStarStatsError("AllStar stats response exceeded 2 MB")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AllStarStatsError("AllStar stats response was not valid JSON") from error
    if not isinstance(payload, dict):
        raise AllStarStatsError("AllStar stats response was not an object")
    return parse_allstar_stats(payload)


def favorite_public_targets(session: Session) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    rows = session.execute(select(Favorite.home_node, Favorite.target_identifier)).all()
    for home_node, target in rows:
        if is_public_node(target):
            grouped.setdefault(target, []).append(home_node)
    return grouped


def store_allstar_snapshot(
    session: Session,
    home_nodes: Iterable[str],
    values: dict[str, object],
) -> None:
    identifier = str(values["remote_identifier"])
    fetched_at = values["fetched_at"]
    assert isinstance(fetched_at, datetime)
    for home_node in home_nodes:
        snapshot = session.scalar(
            select(FavoriteStatsSnapshot).where(
                FavoriteStatsSnapshot.home_node == home_node,
                FavoriteStatsSnapshot.remote_identifier == identifier,
            )
        )
        is_new = snapshot is None
        if snapshot is None:
            snapshot = FavoriteStatsSnapshot(home_node=home_node, remote_identifier=identifier)
            session.add(snapshot)
        previous_keyups = snapshot.total_keyups
        previous_tx = snapshot.total_tx_seconds
        for field in (
            "callsign",
            "description",
            "location",
            "active",
            "keyed",
            "total_keyups",
            "total_tx_seconds",
            "total_kerchunks",
            "uptime_seconds",
            "link_count",
            "topology_json",
            "source_reported_at",
            "fetched_at",
        ):
            setattr(snapshot, field, values[field])
        if snapshot.keyed or (
            not is_new
            and (snapshot.total_keyups > previous_keyups or snapshot.total_tx_seconds > previous_tx)
        ):
            snapshot.last_activity_at = fetched_at
    session.commit()


def refresh_target(
    session_factory: Callable[[], Session],
    target: str,
    home_nodes: Iterable[str],
    *,
    base_url: str,
    timeout_seconds: float,
) -> None:
    values = fetch_allstar_stats(
        target, base_url=base_url, timeout_seconds=timeout_seconds
    )
    with session_factory() as session:
        store_allstar_snapshot(session, home_nodes, values)
