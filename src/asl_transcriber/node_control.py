from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from asl_transcriber.ami import AmiFrame, AmiResponse

KNOWN_LINK_MODES = {"T": "transceive", "R": "receive", "L": "local_monitor", "C": "connecting"}


@dataclass(frozen=True)
class AdjacentLink:
    """One directly connected app_rpt peer, keyed by its complete identifier."""

    identifier: str
    node_number: str | None = None
    callsign: str | None = None
    display_name: str = ""
    peer: str | None = None
    direction: str | None = None
    link_mode: str | None = None
    connection_state: str = "established"
    keyed: bool | None = None
    seconds_since_keyed: int | None = None
    seconds_since_unkeyed: int | None = None
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    stale: bool = True
    source: str = "rpt_alinks"

    @property
    def mode(self) -> str:
        """Compatibility name used by the original node-control foundation."""
        return self.link_mode or ""

    @property
    def mode_name(self) -> str:
        return KNOWN_LINK_MODES.get(self.mode, "unknown")


@dataclass
class NodeState:
    home_node: str
    links: dict[str, AdjacentLink] = field(default_factory=dict)
    topology: list[str] = field(default_factory=list)
    local_rx_keyed: bool = False
    transmitter_keyed: bool = False
    ami_state: str = "disconnected"
    stale: bool = True
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def keyed_links(self) -> list[AdjacentLink]:
        return [link for link in self.links.values() if link.keyed is True]

    @property
    def collision(self) -> bool:
        return len(self.keyed_links) > 1


@dataclass(frozen=True)
class RemoteKeyTransition:
    event: str
    home_node: str
    remote_identifier: str
    timestamp: datetime
    duration_seconds: int | None = None


def _identity_fields(identifier: str) -> tuple[str | None, str | None, str]:
    if identifier.isdigit():
        return identifier, None, identifier
    return None, identifier, identifier


def parse_alinks(value: str, *, now: datetime | None = None) -> list[AdjacentLink]:
    """Parse RPT_ALINKS from the right, preserving nonnumeric IDs and unknown modes."""
    timestamp = now or datetime.now(UTC)
    normalized = value.strip()
    if normalized.casefold().startswith("rpt_alinks="):
        normalized = normalized.split("=", 1)[1]
    tokens = [token.strip() for token in normalized.split(",") if token.strip()]
    if tokens and tokens[0].isdigit():
        tokens = tokens[1:]
    links: list[AdjacentLink] = []
    for token in tokens:
        if len(token) < 3:
            continue
        identifier = token[:-2]
        if not identifier:
            continue
        mode = token[-2].upper()
        key_flag = token[-1].upper()
        node_number, callsign, display_name = _identity_fields(identifier)
        links.append(
            AdjacentLink(
                identifier=identifier,
                node_number=node_number,
                callsign=callsign,
                display_name=display_name,
                link_mode=mode,
                keyed=True if key_flag == "K" else False if key_flag == "U" else None,
                connection_state="connecting" if mode == "C" else "established",
                connected_at=timestamp,
                updated_at=timestamp,
                stale=False,
                source="rpt_alinks",
            )
        )
    return links


def _duration(value: str) -> timedelta:
    parts = value.split(":")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid XStat duration: {value}")
    hours, minutes, seconds = (int(part) for part in parts)
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def parse_xstat_conn(value: str, *, now: datetime | None = None) -> AdjacentLink:
    """Parse a fixed-width XStat Conn row using its stable right-hand fields."""
    parts = value.split()
    if len(parts) < 5:
        raise ValueError(f"invalid XStat Conn row: {value}")
    state = parts[-1].casefold()
    if state not in {"connecting", "established"}:
        raise ValueError(f"invalid XStat connection state: {parts[-1]}")
    connected_for = _duration(parts[-2])
    direction = parts[-3].casefold()
    if direction not in {"in", "out"}:
        raise ValueError(f"invalid XStat direction: {parts[-3]}")
    if not parts[-4].lstrip("-").isdigit():
        raise ValueError(f"invalid XStat reconnect count: {parts[-4]}")
    identifier = parts[0]
    peer_text = " ".join(parts[1:-4]).strip()
    peer = None if peer_text.casefold() in {"", "(none)", "none"} else peer_text
    timestamp = now or datetime.now(UTC)
    node_number, callsign, display_name = _identity_fields(identifier)
    return AdjacentLink(
        identifier=identifier,
        node_number=node_number,
        callsign=callsign,
        display_name=display_name,
        peer=peer,
        direction=direction,
        connection_state=state,
        connected_at=timestamp - connected_for,
        updated_at=timestamp,
        stale=False,
        source="xstat",
    )


def parse_sawstat_conn(value: str) -> tuple[str, bool, int | None, int | None]:
    parts = value.split()
    if len(parts) != 4 or any(not part.lstrip("-").isdigit() for part in parts[1:]):
        raise ValueError(f"invalid SawStat Conn row: {value}")
    keyed, since_keyed, since_unkeyed = (int(part) for part in parts[1:])
    return (
        parts[0],
        keyed != 0,
        None if since_keyed < 0 else since_keyed,
        (None if since_unkeyed < 0 else since_unkeyed),
    )


def parse_xstat_snapshot(
    xstat: AmiResponse,
    sawstat: AmiResponse,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, AdjacentLink], list[str]]:
    if not xstat.success:
        raise ValueError("XStat action was not successful")
    if not sawstat.success:
        raise ValueError("SawStat action was not successful")
    timestamp = now or datetime.now(UTC)
    keyed = {
        row[0]: row[1:] for value in sawstat.values("Conn") for row in [parse_sawstat_conn(value)]
    }
    links: dict[str, AdjacentLink] = {}
    for value in xstat.values("Conn"):
        link = parse_xstat_conn(value, now=timestamp)
        if link.identifier in links:
            raise ValueError(f"duplicate XStat identifier: {link.identifier}")
        key_state = keyed.get(link.identifier)
        if key_state is not None:
            link = replace(
                link,
                keyed=key_state[0],
                seconds_since_keyed=key_state[1],
                seconds_since_unkeyed=key_state[2],
            )
        links[link.identifier] = link
    topology: list[str] = []
    for value in xstat.values("LinkedNodes"):
        if value.casefold() != "<none>":
            topology.extend(item.strip() for item in value.split(",") if item.strip())
    return links, topology


def _event_value(frame: AmiFrame) -> str:
    return frame.get("EventValue") or frame.get("Value")


def normalize_app_rpt_event(state: NodeState, frame: AmiFrame) -> NodeState:
    """Apply native fast-path events; XStat reconciliation remains authoritative."""
    event = (frame.event or "").upper()
    timestamp = datetime.now(UTC)
    next_state = NodeState(
        home_node=state.home_node,
        links=dict(state.links),
        topology=list(state.topology),
        local_rx_keyed=state.local_rx_keyed,
        transmitter_keyed=state.transmitter_keyed,
        ami_state=state.ami_state,
        stale=state.stale,
        updated_at=timestamp,
    )
    if event == "RPT_ALINKS":
        value = _event_value(frame).strip()
        # app_rpt can emit RPT_ALINKS without a value while link state is changing.
        # That is not an authoritative empty snapshot; an explicit "0" is.
        if value:
            parsed = parse_alinks(value, now=timestamp)
            next_state.links = {link.identifier: link for link in parsed}
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
    next_state = state
    for name, value in values:
        frame = AmiFrame({"event": [name], "eventvalue": [value]})
        next_state = normalize_app_rpt_event(next_state, frame)
    return next_state
