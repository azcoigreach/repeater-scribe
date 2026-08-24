from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from asl_transcriber.ami import AmiFrame

KNOWN_LINK_MODES = {"T": "transceive", "R": "receive", "L": "local_monitor", "C": "connecting"}


@dataclass(frozen=True)
class AdjacentLink:
    identifier: str
    mode: str
    keyed: bool

    @property
    def mode_name(self) -> str:
        return KNOWN_LINK_MODES.get(self.mode, "unknown")


@dataclass
class NodeState:
    home_node: str
    links: dict[str, AdjacentLink] = field(default_factory=dict)
    local_rx_keyed: bool = False
    transmitter_keyed: bool = False
    ami_state: str = "disconnected"
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def keyed_links(self) -> list[AdjacentLink]:
        return [link for link in self.links.values() if link.keyed]

    @property
    def collision(self) -> bool:
        return len(self.keyed_links) > 1


def parse_alinks(value: str) -> list[AdjacentLink]:
    """Parse app_rpt link tokens from the right-hand mode/key flags."""
    tokens = [token.strip() for token in value.split(",") if token.strip()]
    if tokens and tokens[0].isdigit():
        tokens = tokens[1:]
    links: list[AdjacentLink] = []
    for token in tokens:
        if len(token) < 3:
            continue
        keyed = token[-1].upper() == "K"
        mode = token[-2].upper()
        identifier = token[:-2]
        if identifier:
            links.append(AdjacentLink(identifier=identifier, mode=mode, keyed=keyed))
    return links


def _event_value(frame: AmiFrame) -> str:
    return frame.get("EventValue") or frame.get("Value")


def normalize_app_rpt_event(state: NodeState, frame: AmiFrame) -> NodeState:
    """Apply one native app_rpt event immediately, without attribution guesses."""
    event = (frame.event or "").upper()
    next_state = NodeState(
        home_node=state.home_node,
        links=dict(state.links),
        local_rx_keyed=state.local_rx_keyed,
        transmitter_keyed=state.transmitter_keyed,
        ami_state=state.ami_state,
        updated_at=datetime.now(UTC),
    )
    if event == "RPT_ALINKS":
        next_state.links = {link.identifier: link for link in parse_alinks(_event_value(frame))}
    elif event == "RPT_RXKEYED":
        next_state.local_rx_keyed = _event_value(frame) == "1"
    elif event == "RPT_TXKEYED":
        next_state.transmitter_keyed = _event_value(frame) == "1"
    return next_state


def keyed_sources(state: NodeState) -> list[tuple[str, str]]:
    """Return source kind/id pairs; local RF intentionally has no operator identity."""
    sources = [("adjacent_node", link.identifier) for link in state.keyed_links]
    if state.local_rx_keyed:
        sources.append(("local_rf", "unknown"))
    return sources


def apply_status_values(state: NodeState, values: Iterable[tuple[str, str]]) -> NodeState:
    """Apply CLI compatibility values while keeping native event semantics."""
    next_state = state
    for name, value in values:
        frame = AmiFrame({"event": [name], "eventvalue": [value]})
        next_state = normalize_app_rpt_event(next_state, frame)
    return next_state
