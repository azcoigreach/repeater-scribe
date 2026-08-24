from __future__ import annotations

import asyncio

from asl_transcriber.config import Settings
from asl_transcriber.node_control import parse_alinks
from asl_transcriber.node_service import NodeStateService


def test_failed_snapshot_preserves_rows_and_marks_them_stale() -> None:
    async def scenario() -> None:
        service = NodeStateService(Settings(ami_node_id="668390", ami_secret="secret"))
        state = service.state("668390")
        state.links = {link.identifier: link for link in parse_alinks("1,674982TU")}
        state.stale = False
        original_connected_at = state.links["674982"].connected_at

        await service._mark_stale("668390")

        retained = service.state("668390").links["674982"]
        assert retained.stale is True
        assert retained.connected_at == original_connected_at

    asyncio.run(scenario())
