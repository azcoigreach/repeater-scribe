from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from asl_transcriber.allstar_stats import (
    AllStarStatsError,
    fetch_allstar_stats,
    is_public_node,
    store_allstar_snapshot,
)
from asl_transcriber.models import (
    Favorite,
    TopologyCrawl,
    TopologyEdgeSnapshot,
    TopologyNodeSnapshot,
)

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _list(value: str) -> list[Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _dict(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dump(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _integer(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _topology_parts(values: dict[str, object]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = values.get("topology_json")
    parsed = _dict(str(raw)) if raw is not None else {}
    root = parsed.get("root")
    links = parsed.get("links")
    return (
        root if isinstance(root, dict) else {},
        [item for item in links if isinstance(item, dict)] if isinstance(links, list) else [],
    )


def ensure_topology_crawl(
    session: Session,
    home_node: str,
    root_identifier: str,
    *,
    max_nodes: int = 200,
    max_depth: int = 12,
    restart: bool = False,
    now: datetime | None = None,
) -> TopologyCrawl:
    if not is_public_node(root_identifier):
        raise ValueError("Topology roots must be public numeric AllStar nodes")
    timestamp = now or datetime.now(UTC)
    crawl = session.scalar(
        select(TopologyCrawl).where(
            TopologyCrawl.home_node == home_node,
            TopologyCrawl.root_identifier == root_identifier,
        )
    )
    if crawl is None:
        crawl = TopologyCrawl(
            home_node=home_node,
            root_identifier=root_identifier,
            queue_json=_dump([{"identifier": root_identifier, "depth": 0, "attempts": 0}]),
            seen_json=_dump([root_identifier]),
            processed_json="[]",
            max_nodes=max_nodes,
            max_depth=max_depth,
            started_at=timestamp,
            updated_at=timestamp,
        )
        session.add(crawl)
    elif restart:
        crawl.status = "queued"
        crawl.queue_json = _dump([{"identifier": root_identifier, "depth": 0, "attempts": 0}])
        crawl.seen_json = _dump([root_identifier])
        crawl.processed_json = "[]"
        crawl.queried_count = 0
        crawl.max_nodes = max_nodes
        crawl.max_depth = max_depth
        crawl.started_at = timestamp
        crawl.updated_at = timestamp
        crawl.completed_at = None
        crawl.next_refresh_at = None
        crawl.limit_reason = None
        crawl.last_error = None
    session.commit()
    session.refresh(crawl)
    return crawl


def seed_favorite_crawls(
    session: Session, *, max_nodes: int, max_depth: int, now: datetime | None = None
) -> int:
    created = 0
    existing = {
        (crawl.home_node, crawl.root_identifier)
        for crawl in session.scalars(select(TopologyCrawl)).all()
    }
    for home_node, target in session.execute(
        select(Favorite.home_node, Favorite.target_identifier)
    ).all():
        if is_public_node(target) and (home_node, target) not in existing:
            ensure_topology_crawl(
                session,
                home_node,
                target,
                max_nodes=max_nodes,
                max_depth=max_depth,
                now=now,
            )
            existing.add((home_node, target))
            created += 1
    return created


@dataclass(frozen=True)
class CrawlWork:
    crawl_id: str
    home_node: str
    root_identifier: str
    identifier: str
    depth: int
    attempts: int
    favorite_refresh: bool = False


def next_crawl_work(
    session: Session,
    *,
    refresh_seconds: int,
    now: datetime | None = None,
) -> CrawlWork | None:
    timestamp = now or datetime.now(UTC)
    crawls = session.scalars(select(TopologyCrawl).order_by(TopologyCrawl.updated_at)).all()
    for crawl in crawls:
        if (
            crawl.status in {"complete", "limited"}
            and crawl.next_refresh_at is not None
            and _utc(crawl.next_refresh_at) <= timestamp
        ):
            ensure_topology_crawl(
                session,
                crawl.home_node,
                crawl.root_identifier,
                max_nodes=crawl.max_nodes,
                max_depth=crawl.max_depth,
                restart=True,
                now=timestamp,
            )
            session.refresh(crawl)
        queue = _list(crawl.queue_json)
        if crawl.status not in {"queued", "crawling"} or not queue:
            continue
        raw = queue.pop(0)
        if not isinstance(raw, dict):
            crawl.queue_json = _dump(queue)
            session.commit()
            continue
        crawl.status = "crawling"
        crawl.queue_json = _dump(queue)
        crawl.updated_at = timestamp
        session.commit()
        return CrawlWork(
            crawl.id,
            crawl.home_node,
            crawl.root_identifier,
            str(raw.get("identifier", "")),
            int(raw.get("depth", 0)),
            int(raw.get("attempts", 0)),
        )
    return None


def cached_topology_values(
    session: Session,
    work: CrawlWork,
    *,
    cache_seconds: int,
    now: datetime | None = None,
) -> dict[str, object] | None:
    timestamp = now or datetime.now(UTC)
    node = session.scalar(
        select(TopologyNodeSnapshot).where(
            TopologyNodeSnapshot.home_node == work.home_node,
            TopologyNodeSnapshot.identifier == work.identifier,
        )
    )
    if (
        node is None
        or node.fetched_at is None
        or timestamp - _utc(node.fetched_at) > timedelta(seconds=cache_seconds)
    ):
        return None
    metadata = _dict(node.metadata_json)
    neighbors = _list(node.neighbors_json)
    return {
        "remote_identifier": node.identifier,
        "callsign": metadata.get("callsign"),
        "description": metadata.get("frequency"),
        "location": metadata.get("location"),
        "active": node.active,
        "keyed": node.keyed,
        "total_keyups": node.total_keyups,
        "total_tx_seconds": node.total_tx_seconds,
        "total_kerchunks": node.total_kerchunks,
        "uptime_seconds": node.uptime_seconds,
        "link_count": len(neighbors),
        "source_reported_at": node.source_reported_at,
        "fetched_at": node.fetched_at,
        "topology_json": _dump(
            {"root": metadata, "links": neighbors}
        ),
    }


def _upsert_node(
    session: Session, home_node: str, identifier: str, metadata: dict[str, Any]
) -> TopologyNodeSnapshot:
    node = session.scalar(
        select(TopologyNodeSnapshot).where(
            TopologyNodeSnapshot.home_node == home_node,
            TopologyNodeSnapshot.identifier == identifier,
        )
    )
    if node is None:
        node = TopologyNodeSnapshot(home_node=home_node, identifier=identifier)
        session.add(node)
    current = _dict(node.metadata_json)
    current.update({key: value for key, value in metadata.items() if value is not None})
    current["identifier"] = identifier
    node.metadata_json = _dump(current)
    return node


def apply_topology_snapshot(
    session: Session,
    work: CrawlWork,
    values: dict[str, object],
    *,
    refresh_seconds: int,
    queried: bool = True,
    advance_crawl: bool = True,
    now: datetime | None = None,
) -> None:
    timestamp = now or datetime.now(UTC)
    crawl = session.get(TopologyCrawl, work.crawl_id)
    if crawl is None:
        return
    root_metadata, links = _topology_parts(values)
    node = _upsert_node(session, work.home_node, work.identifier, root_metadata)
    node.neighbors_json = _dump(links)
    node.active = bool(values.get("active"))
    node.keyed = bool(values.get("keyed"))
    node.total_keyups = _integer(values.get("total_keyups", 0))
    node.total_tx_seconds = _integer(values.get("total_tx_seconds", 0))
    node.total_kerchunks = _integer(values.get("total_kerchunks", 0))
    node.uptime_seconds = _integer(values.get("uptime_seconds", 0))
    source_reported_at = values.get("source_reported_at")
    node.source_reported_at = (
        source_reported_at if isinstance(source_reported_at, datetime) else None
    )
    fetched_at = values.get("fetched_at")
    node.fetched_at = fetched_at if isinstance(fetched_at, datetime) else timestamp
    node.last_error = None

    current_neighbors: set[str] = set()
    for link in links:
        identifier = str(link.get("identifier", "")).strip()
        if not identifier or identifier == work.identifier:
            continue
        current_neighbors.add(identifier)
        _upsert_node(session, work.home_node, identifier, link)
        node_a, node_b = sorted((work.identifier, identifier))
        edge = session.scalar(
            select(TopologyEdgeSnapshot).where(
                TopologyEdgeSnapshot.home_node == work.home_node,
                TopologyEdgeSnapshot.node_a == node_a,
                TopologyEdgeSnapshot.node_b == node_b,
            )
        )
        if edge is None:
            edge = TopologyEdgeSnapshot(
                home_node=work.home_node,
                node_a=node_a,
                node_b=node_b,
                first_seen_at=timestamp,
                last_seen_at=timestamp,
            )
            session.add(edge)
        reporters = {str(item) for item in _list(edge.reporters_json)}
        reporters.add(work.identifier)
        modes = _dict(edge.modes_json)
        modes[work.identifier] = str(link.get("mode") or "linked")
        edge.reporters_json = _dump(sorted(reporters))
        edge.modes_json = _dump(modes)
        edge.last_seen_at = timestamp
        edge.stale_at = None

    previous_edges = session.scalars(
        select(TopologyEdgeSnapshot).where(
            TopologyEdgeSnapshot.home_node == work.home_node,
            or_(
                TopologyEdgeSnapshot.node_a == work.identifier,
                TopologyEdgeSnapshot.node_b == work.identifier,
            ),
        )
    ).all()
    for edge in previous_edges:
        other = edge.node_b if edge.node_a == work.identifier else edge.node_a
        reporters = {str(item) for item in _list(edge.reporters_json)}
        if other not in current_neighbors and work.identifier in reporters:
            reporters.remove(work.identifier)
            modes = _dict(edge.modes_json)
            modes.pop(work.identifier, None)
            edge.reporters_json = _dump(sorted(reporters))
            edge.modes_json = _dump(modes)
            if not reporters:
                edge.stale_at = timestamp

    if not advance_crawl:
        session.commit()
        return

    seen = {str(item) for item in _list(crawl.seen_json)}
    processed = {str(item) for item in _list(crawl.processed_json)}
    processed.add(work.identifier)
    queue = [item for item in _list(crawl.queue_json) if isinstance(item, dict)]
    queued_ids = {str(item.get("identifier")) for item in queue}
    limited = False
    for identifier in current_neighbors:
        if identifier not in seen:
            if work.depth >= crawl.max_depth:
                crawl.limit_reason = "max_depth"
                limited = True
                continue
            if len(seen) >= crawl.max_nodes:
                crawl.limit_reason = "max_nodes"
                limited = True
                continue
            seen.add(identifier)
        if (
            is_public_node(identifier)
            and work.depth < crawl.max_depth
            and identifier not in queued_ids
            and identifier not in processed
            and identifier != work.identifier
        ):
            queue.append({"identifier": identifier, "depth": work.depth + 1, "attempts": 0})
            queued_ids.add(identifier)

    if queried:
        crawl.queried_count += 1
    crawl.queue_json = _dump(queue)
    crawl.seen_json = _dump(sorted(seen))
    crawl.processed_json = _dump(sorted(processed))
    crawl.updated_at = timestamp
    crawl.last_error = None
    if not queue:
        if len(seen) >= crawl.max_nodes and crawl.limit_reason is None:
            crawl.limit_reason = "max_nodes"
        crawl.status = "limited" if limited or crawl.limit_reason else "complete"
        crawl.completed_at = timestamp
        crawl.next_refresh_at = timestamp + timedelta(seconds=refresh_seconds)
    session.commit()


def fail_topology_work(
    session: Session,
    work: CrawlWork,
    error: str,
    *,
    refresh_seconds: int,
    advance_crawl: bool = True,
    now: datetime | None = None,
) -> None:
    timestamp = now or datetime.now(UTC)
    crawl = session.get(TopologyCrawl, work.crawl_id)
    if crawl is None:
        return
    node = _upsert_node(session, work.home_node, work.identifier, {})
    node.last_error = error[:2_000]
    if not advance_crawl:
        session.commit()
        return
    queue = [item for item in _list(crawl.queue_json) if isinstance(item, dict)]
    if work.attempts < 2:
        queue.append(
            {
                "identifier": work.identifier,
                "depth": work.depth,
                "attempts": work.attempts + 1,
            }
        )
    crawl.queue_json = _dump(queue)
    crawl.last_error = error[:2_000]
    crawl.updated_at = timestamp
    if not queue:
        crawl.status = "complete"
        crawl.completed_at = timestamp
        crawl.next_refresh_at = timestamp + timedelta(seconds=refresh_seconds)
    session.commit()


def serialize_topology(
    session: Session,
    home_node: str,
    root_identifier: str,
    *,
    stale_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, object]:
    timestamp = now or datetime.now(UTC)
    crawl = session.scalar(
        select(TopologyCrawl).where(
            TopologyCrawl.home_node == home_node,
            TopologyCrawl.root_identifier == root_identifier,
        )
    )
    if crawl is None:
        return {
            "root": root_identifier,
            "status": "not_started",
            "complete": False,
            "limited": False,
            "progress": {"discovered": 0, "queried": 0, "queued": 0},
            "nodes": [],
            "edges": [],
        }
    seen = {str(item) for item in _list(crawl.seen_json)}
    node_rows = session.scalars(
        select(TopologyNodeSnapshot).where(
            TopologyNodeSnapshot.home_node == home_node,
            TopologyNodeSnapshot.identifier.in_(seen),
        )
    ).all()
    edge_rows = session.scalars(
        select(TopologyEdgeSnapshot).where(TopologyEdgeSnapshot.home_node == home_node)
    ).all()
    usable_edges = [edge for edge in edge_rows if edge.node_a in seen and edge.node_b in seen]

    adjacency: dict[str, set[str]] = {identifier: set() for identifier in seen}
    for edge in usable_edges:
        adjacency[edge.node_a].add(edge.node_b)
        adjacency[edge.node_b].add(edge.node_a)
    depths = {root_identifier: 0}
    pending = deque([root_identifier])
    while pending:
        source = pending.popleft()
        for target in adjacency.get(source, set()):
            if target not in depths:
                depths[target] = depths[source] + 1
                pending.append(target)

    nodes: list[dict[str, object]] = []
    for row in node_rows:
        metadata = _dict(row.metadata_json)
        fetched_at = _utc(row.fetched_at) if row.fetched_at else None
        nodes.append(
            {
                **metadata,
                "identifier": row.identifier,
                "root": row.identifier == root_identifier,
                "depth": depths.get(row.identifier),
                "active": row.active,
                "keyed": row.keyed,
                "keyup_count": row.total_keyups,
                "total_tx_milliseconds": row.total_tx_seconds * 1_000,
                "kerchunk_count": row.total_kerchunks,
                "uptime_seconds": row.uptime_seconds,
                "reported_at": (
                    _utc(row.source_reported_at).isoformat() if row.source_reported_at else None
                ),
                "fetched_at": fetched_at.isoformat() if fetched_at else None,
                "stale": bool(
                    fetched_at and timestamp - fetched_at > timedelta(seconds=stale_seconds)
                ),
                "error": row.last_error,
            }
        )
    nodes.sort(
        key=lambda item: (
            item.get("depth") is None,
            item.get("depth") or 0,
            str(item["identifier"]),
        )
    )
    edges = []
    for edge in usable_edges:
        reporters = [str(item) for item in _list(edge.reporters_json)]
        edges.append(
            {
                "source": edge.node_a,
                "target": edge.node_b,
                "reported_by": reporters,
                "confirmed": len(reporters) >= 2,
                "provisional": len(reporters) == 1,
                "stale": edge.stale_at is not None,
                "modes": _dict(edge.modes_json),
                "last_seen_at": _utc(edge.last_seen_at).isoformat(),
            }
        )
    queue = _list(crawl.queue_json)
    return {
        "root": root_identifier,
        "status": crawl.status,
        "complete": crawl.status in {"complete", "limited"},
        "limited": crawl.status == "limited",
        "limit_reason": crawl.limit_reason,
        "progress": {
            "discovered": len(seen),
            "queried": crawl.queried_count,
            "queued": len(queue),
            "max_nodes": crawl.max_nodes,
            "max_depth": crawl.max_depth,
        },
        "updated_at": _utc(crawl.updated_at).isoformat(),
        "last_error": crawl.last_error,
        "nodes": nodes,
        "edges": edges,
    }


class TopologyService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        base_url: str,
        request_interval_seconds: float,
        timeout_seconds: float,
        favorite_refresh_seconds: int,
        max_nodes: int,
        max_depth: int,
        refresh_seconds: int,
        cache_seconds: int,
        fetcher: Callable[..., dict[str, object]] = fetch_allstar_stats,
    ) -> None:
        self.session_factory = session_factory
        self.base_url = base_url
        self.request_interval_seconds = request_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.favorite_refresh_seconds = favorite_refresh_seconds
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.refresh_seconds = refresh_seconds
        self.cache_seconds = cache_seconds
        self.fetcher = fetcher
        self._favorite_attempted_at: dict[tuple[str, str], datetime] = {}
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()

    def _favorite_work(self, session: Session) -> CrawlWork | None:
        timestamp = datetime.now(UTC)
        due_before = timestamp - timedelta(seconds=self.favorite_refresh_seconds)
        candidates: list[tuple[datetime, str, str]] = []
        for home_node, target in session.execute(
            select(Favorite.home_node, Favorite.target_identifier)
        ).all():
            if not is_public_node(target):
                continue
            node = session.scalar(
                select(TopologyNodeSnapshot).where(
                    TopologyNodeSnapshot.home_node == home_node,
                    TopologyNodeSnapshot.identifier == target,
                )
            )
            last_attempt = self._favorite_attempted_at.get((home_node, target))
            last_fetch = _utc(node.fetched_at) if node and node.fetched_at else None
            latest = max(
                (value for value in (last_attempt, last_fetch) if value is not None),
                default=datetime.min.replace(tzinfo=UTC),
            )
            if latest <= due_before:
                candidates.append((latest, home_node, target))
        if not candidates:
            return None
        _, home_node, target = min(candidates)
        crawl = session.scalar(
            select(TopologyCrawl).where(
                TopologyCrawl.home_node == home_node,
                TopologyCrawl.root_identifier == target,
            )
        )
        if crawl is None:
            return None
        self._favorite_attempted_at[(home_node, target)] = timestamp
        return CrawlWork(crawl.id, home_node, target, target, 0, 0, favorite_refresh=True)

    def _seed_and_take(self) -> CrawlWork | None:
        with self.session_factory() as session:
            seed_favorite_crawls(session, max_nodes=self.max_nodes, max_depth=self.max_depth)
            favorite_work = self._favorite_work(session)
            if favorite_work is not None:
                return favorite_work
            return next_crawl_work(session, refresh_seconds=self.refresh_seconds)

    def _cached(self, work: CrawlWork) -> dict[str, object] | None:
        if work.favorite_refresh:
            return None
        with self.session_factory() as session:
            return cached_topology_values(session, work, cache_seconds=self.cache_seconds)

    def _store(self, work: CrawlWork, values: dict[str, object], *, queried: bool) -> None:
        with self.session_factory() as session:
            is_favorite = session.scalar(
                select(Favorite.id).where(
                    Favorite.home_node == work.home_node,
                    Favorite.target_identifier == work.identifier,
                )
            )
            if is_favorite is not None:
                store_allstar_snapshot(session, [work.home_node], values)
            apply_topology_snapshot(
                session,
                work,
                values,
                refresh_seconds=self.refresh_seconds,
                queried=queried,
                advance_crawl=not work.favorite_refresh,
            )

    def _fail(self, work: CrawlWork, error: str) -> None:
        with self.session_factory() as session:
            fail_topology_work(
                session,
                work,
                error,
                refresh_seconds=self.refresh_seconds,
                advance_crawl=not work.favorite_refresh,
            )

    def _publish(self, work: CrawlWork) -> None:
        event: dict[str, object] = {
            "home_node": work.home_node,
            "root": work.root_identifier,
            "updated_node": work.identifier,
        }
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    async def run(self) -> None:
        while True:
            work = await asyncio.to_thread(self._seed_and_take)
            if work is None:
                await asyncio.sleep(min(self.request_interval_seconds, 2.0))
                continue
            cached = await asyncio.to_thread(self._cached, work)
            if cached is not None:
                await asyncio.to_thread(self._store, work, cached, queried=False)
                self._publish(work)
                await asyncio.sleep(0)
                continue
            try:
                values = await asyncio.to_thread(
                    self.fetcher,
                    work.identifier,
                    base_url=self.base_url,
                    timeout_seconds=self.timeout_seconds,
                )
                await asyncio.to_thread(self._store, work, values, queried=True)
            except AllStarStatsError as error:
                logger.warning("Topology fetch failed for %s: %s", work.identifier, error)
                await asyncio.to_thread(self._fail, work, str(error))
            except Exception as error:
                logger.exception("Topology crawl failed for %s", work.identifier)
                await asyncio.to_thread(self._fail, work, str(error))
            self._publish(work)
            await asyncio.sleep(self.request_interval_seconds)

    async def events(self, heartbeat_seconds: float = 15.0) -> AsyncIterator[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=20)
        self._subscribers.add(queue)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except TimeoutError:
                    yield {"heartbeat": True}
        finally:
            self._subscribers.discard(queue)
