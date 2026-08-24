from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from asl_transcriber.models import Favorite, RemoteNodeStat
from asl_transcriber.node_control import AdjacentLink, RemoteKeyTransition


class FavoriteNotFound(LookupError):
    pass


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def create_favorite(
    session: Session,
    *,
    home_node: str,
    target_identifier: str,
    label: str,
    callsign: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> Favorite:
    existing = session.scalar(
        select(Favorite).where(
            Favorite.home_node == home_node,
            Favorite.target_identifier == target_identifier,
        )
    )
    if existing is not None:
        return existing
    favorite = Favorite(
        home_node=home_node,
        target_identifier=target_identifier,
        label=label,
        callsign_override=callsign,
        description_override=description,
        location_override=location,
    )
    session.add(favorite)
    session.commit()
    session.refresh(favorite)
    return favorite


def update_favorite(session: Session, home_node: str, favorite_id: str, values: Mapping[str, object]) -> Favorite:
    favorite = favorite_by_id(session, home_node, favorite_id)
    fields = {
        "label": "label",
        "callsign": "callsign_override",
        "description": "description_override",
        "location": "location_override",
        "group_name": "group_name",
        "sort_order": "sort_order",
        "default_connection_mode": "default_connection_mode",
        "permanent": "permanent",
        "exclusive_connect": "exclusive_connect",
    }
    for source, target in fields.items():
        if source in values:
            setattr(favorite, target, values[source])
    session.commit()
    session.refresh(favorite)
    return favorite


def delete_favorite(session: Session, home_node: str, favorite_id: str) -> None:
    favorite = favorite_by_id(session, home_node, favorite_id)
    session.delete(favorite)
    session.commit()


def favorite_by_id(session: Session, home_node: str, favorite_id: str) -> Favorite:
    favorite = session.scalar(
        select(Favorite).where(Favorite.id == favorite_id, Favorite.home_node == home_node)
    )
    if favorite is None:
        raise FavoriteNotFound(f"Favorite {favorite_id} was not found")
    return favorite


def list_favorite_items(
    session: Session,
    home_node: str,
    live_links: Mapping[str, AdjacentLink],
    *,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    favorites = session.scalars(
        select(Favorite)
        .where(Favorite.home_node == home_node)
        .order_by(Favorite.sort_order, Favorite.label, Favorite.target_identifier)
    ).all()
    stats = {
        stat.remote_identifier: stat
        for stat in session.scalars(
            select(RemoteNodeStat).where(RemoteNodeStat.home_node == home_node)
        ).all()
    }
    timestamp = now or datetime.now(UTC)
    return [
        serialize_favorite(
            favorite,
            stats.get(favorite.target_identifier),
            live_links.get(favorite.target_identifier),
            now=timestamp,
        )
        for favorite in favorites
    ]


def serialize_favorite(
    favorite: Favorite,
    stat: RemoteNodeStat | None,
    live_link: AdjacentLink | None,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    timestamp = now or datetime.now(UTC)
    tx_milliseconds = stat.total_tx_milliseconds if stat else 0
    if stat is not None and stat.active_keyed_at is not None:
        tx_milliseconds += max(
            0, int((timestamp - _utc(stat.active_keyed_at)).total_seconds() * 1000)
        )
    callsign = favorite.callsign_override
    if not callsign and live_link is not None:
        callsign = live_link.callsign
    return {
        "id": favorite.id,
        "home_node": favorite.home_node,
        "target_identifier": favorite.target_identifier,
        "label": favorite.label,
        "callsign": callsign,
        "description": favorite.description_override,
        "location": favorite.location_override,
        "group_name": favorite.group_name,
        "sort_order": favorite.sort_order,
        "default_connection_mode": favorite.default_connection_mode,
        "permanent": favorite.permanent,
        "exclusive_connect": favorite.exclusive_connect,
        "keyup_count": stat.keyup_count if stat else 0,
        "total_tx_milliseconds": tx_milliseconds,
        "last_keyed_at": stat.last_keyed_at.isoformat() if stat and stat.last_keyed_at else None,
        "last_unkeyed_at": (
            stat.last_unkeyed_at.isoformat() if stat and stat.last_unkeyed_at else None
        ),
        "connected": live_link is not None,
        "keyed": live_link.keyed if live_link is not None else False,
        "connection_state": live_link.connection_state if live_link is not None else None,
        "created_at": favorite.created_at.isoformat(),
        "updated_at": favorite.updated_at.isoformat(),
    }


def record_remote_key_transition(
    session_factory: Callable[[], Session], transition: RemoteKeyTransition
) -> None:
    with session_factory() as session:
        stat = session.scalar(
            select(RemoteNodeStat).where(
                RemoteNodeStat.home_node == transition.home_node,
                RemoteNodeStat.remote_identifier == transition.remote_identifier,
            )
        )
        if stat is None:
            stat = RemoteNodeStat(
                home_node=transition.home_node,
                remote_identifier=transition.remote_identifier,
                keyup_count=0,
                total_tx_milliseconds=0,
                first_seen_at=transition.timestamp,
            )
            session.add(stat)
        if transition.event == "remote_keyed_started":
            if stat.active_keyed_at is None:
                stat.keyup_count += 1
                stat.active_keyed_at = transition.timestamp
            stat.last_keyed_at = transition.timestamp
        elif transition.event == "remote_keyed_ended":
            duration_seconds = transition.duration_seconds
            if duration_seconds is None and stat.active_keyed_at is not None:
                duration_seconds = max(
                    0,
                    int(
                        (
                            transition.timestamp - _utc(stat.active_keyed_at)
                        ).total_seconds()
                    ),
                )
            if duration_seconds is not None:
                stat.total_tx_milliseconds += max(0, duration_seconds * 1000)
            stat.active_keyed_at = None
            stat.last_unkeyed_at = transition.timestamp
        stat.updated_at = transition.timestamp
        session.commit()
