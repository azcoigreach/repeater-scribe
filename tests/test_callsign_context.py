from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from asl_transcriber.database import Base
from asl_transcriber.models import Favorite, FavoriteStatsSnapshot, TopologyNodeSnapshot
from asl_transcriber.transcription.context import DatabaseCallsignProvider


def test_database_callsign_provider_ranks_configured_and_active_local_context(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'callsigns.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)

    with sessions() as session:
        session.add(
            Favorite(
                home_node="668390",
                target_identifier="63916",
                label="Club",
                callsign_override="NY7S",
            )
        )
        session.add(
            FavoriteStatsSnapshot(
                home_node="668390",
                remote_identifier="641890",
                callsign="KE7WIL",
                active=True,
                keyed=True,
            )
        )
        session.add(
            TopologyNodeSnapshot(
                home_node="668390",
                identifier="63916",
                metadata_json=json.dumps({"callsign": "NY7S"}),
                neighbors_json=json.dumps(
                    [{"callsign": "K7TED", "active": True}, {"callsign": "not-a-call"}]
                ),
                active=True,
            )
        )
        session.commit()

    provider = DatabaseCallsignProvider(
        sessions,
        configured_callsigns=("KM7GHS",),
        cache_seconds=30,
    )

    assert provider()[:4] == ("KM7GHS", "KE7WIL", "NY7S", "K7TED")
