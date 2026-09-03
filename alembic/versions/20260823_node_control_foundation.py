"""Create the node-control foundation tables and current application schema.

Revision ID: node_control_foundation
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "node_control_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is intentionally a frozen 0.6.0 snapshot. Do not import application models here.
    tables = (
        "CREATE TABLE nodes (id VARCHAR(36) PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, local_node INTEGER, name VARCHAR(255), is_active BOOLEAN NOT NULL)",
        "CREATE TABLE recordings (id VARCHAR(36) PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, source_path VARCHAR(1024) NOT NULL, content_hash VARCHAR(128), status VARCHAR(64) NOT NULL, local_node INTEGER, source_node INTEGER, file_size INTEGER, duration_seconds FLOAT, deleted_at DATETIME, node_id VARCHAR(36), FOREIGN KEY(node_id) REFERENCES nodes(id))",
        "CREATE TABLE ingestion_jobs (id VARCHAR(36) PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, source_path VARCHAR(1024) NOT NULL, status VARCHAR(64) NOT NULL, attempt_count INTEGER NOT NULL, last_error TEXT, dead_letter BOOLEAN NOT NULL, retry_at DATETIME)",
        "CREATE TABLE transcripts (id VARCHAR(36) PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, job_id VARCHAR(36) NOT NULL UNIQUE, raw_text TEXT NOT NULL, display_text TEXT NOT NULL, language VARCHAR(32), confidence FLOAT, FOREIGN KEY(job_id) REFERENCES ingestion_jobs(id))",
        "CREATE TABLE transmissions (id VARCHAR(36) PRIMARY KEY, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, recording_id VARCHAR(36), status VARCHAR(64) NOT NULL, source_node INTEGER, local_node INTEGER, start_time DATETIME, duration_seconds FLOAT, source_type VARCHAR(64), home_node VARCHAR(32), source_identifier VARCHAR(64), associated_node_callsign VARCHAR(32), operator_callsign VARCHAR(32), attribution_level VARCHAR(32) NOT NULL, ended_at DATETIME, duration_milliseconds INTEGER, close_reason VARCHAR(64), collision BOOLEAN NOT NULL, raw_event_metadata TEXT, deleted_at DATETIME, FOREIGN KEY(recording_id) REFERENCES recordings(id))",
        "CREATE TABLE favorites (id VARCHAR(36) PRIMARY KEY, home_node VARCHAR(32) NOT NULL, target_identifier VARCHAR(64) NOT NULL, label VARCHAR(255) NOT NULL, callsign_override VARCHAR(32), description_override VARCHAR(255), location_override VARCHAR(255), group_name VARCHAR(64), sort_order INTEGER NOT NULL, default_connection_mode VARCHAR(32) NOT NULL, permanent BOOLEAN NOT NULL, exclusive_connect BOOLEAN NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, CONSTRAINT uq_favorite_home_target UNIQUE(home_node, target_identifier))",
        "CREATE TABLE favorite_stats_snapshots (id VARCHAR(36) PRIMARY KEY, home_node VARCHAR(32) NOT NULL, remote_identifier VARCHAR(64) NOT NULL, callsign VARCHAR(32), description VARCHAR(255), location VARCHAR(255), active BOOLEAN NOT NULL, keyed BOOLEAN NOT NULL, total_keyups INTEGER NOT NULL, total_tx_seconds INTEGER NOT NULL, total_kerchunks INTEGER NOT NULL, uptime_seconds INTEGER NOT NULL, link_count INTEGER NOT NULL, topology_json TEXT NOT NULL, source_reported_at DATETIME, last_activity_at DATETIME, fetched_at DATETIME NOT NULL, CONSTRAINT uq_favorite_snapshot_home_target UNIQUE(home_node, remote_identifier))",
        "CREATE TABLE topology_node_snapshots (id VARCHAR(36) PRIMARY KEY, home_node VARCHAR(32) NOT NULL, identifier VARCHAR(64) NOT NULL, metadata_json TEXT NOT NULL, neighbors_json TEXT NOT NULL, active BOOLEAN NOT NULL, keyed BOOLEAN NOT NULL, total_keyups INTEGER NOT NULL, total_tx_seconds INTEGER NOT NULL, uptime_seconds INTEGER NOT NULL, source_reported_at DATETIME, fetched_at DATETIME, last_error TEXT, CONSTRAINT uq_topology_node_home_identifier UNIQUE(home_node, identifier))",
        "CREATE TABLE topology_edge_snapshots (id VARCHAR(36) PRIMARY KEY, home_node VARCHAR(32) NOT NULL, node_a VARCHAR(64) NOT NULL, node_b VARCHAR(64) NOT NULL, reporters_json TEXT NOT NULL, modes_json TEXT NOT NULL, first_seen_at DATETIME NOT NULL, last_seen_at DATETIME NOT NULL, stale_at DATETIME, CONSTRAINT uq_topology_edge_home_pair UNIQUE(home_node, node_a, node_b))",
        "CREATE TABLE topology_crawls (id VARCHAR(36) PRIMARY KEY, home_node VARCHAR(32) NOT NULL, root_identifier VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, queue_json TEXT NOT NULL, seen_json TEXT NOT NULL, processed_json TEXT NOT NULL, queried_count INTEGER NOT NULL, max_nodes INTEGER NOT NULL, max_depth INTEGER NOT NULL, started_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, completed_at DATETIME, next_refresh_at DATETIME, limit_reason VARCHAR(32), last_error TEXT, CONSTRAINT uq_topology_crawl_home_root UNIQUE(home_node, root_identifier))",
        "CREATE TABLE link_sessions (id VARCHAR(36) PRIMARY KEY, home_node VARCHAR(32) NOT NULL, remote_identifier VARCHAR(64) NOT NULL, link_mode VARCHAR(16), direction VARCHAR(32), connected_at DATETIME NOT NULL, disconnected_at DATETIME, disconnect_reason VARCHAR(64), confirmed BOOLEAN NOT NULL)",
        "CREATE TABLE control_audits (id VARCHAR(36) PRIMARY KEY, identity VARCHAR(255) NOT NULL, home_node VARCHAR(32) NOT NULL, operation VARCHAR(64) NOT NULL, target VARCHAR(64), requested_mode VARCHAR(32), timestamp DATETIME NOT NULL, ami_response TEXT, confirmation_result VARCHAR(32), failure_reason TEXT)",
    )
    for statement in tables:
        op.execute(sa.text(statement))
    for table, column in (
        ("recordings", "content_hash"), ("recordings", "status"),
        ("ingestion_jobs", "status"), ("favorites", "home_node"),
        ("topology_node_snapshots", "home_node"), ("topology_node_snapshots", "identifier"),
        ("topology_edge_snapshots", "home_node"), ("topology_edge_snapshots", "node_a"),
        ("topology_edge_snapshots", "node_b"), ("topology_crawls", "home_node"),
        ("topology_crawls", "status"), ("link_sessions", "home_node"),
        ("link_sessions", "remote_identifier"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in (
        "control_audits", "link_sessions", "topology_crawls", "topology_edge_snapshots",
        "topology_node_snapshots", "favorite_stats_snapshots", "favorites", "transmissions",
        "transcripts", "ingestion_jobs", "recordings", "nodes",
    ):
        op.drop_table(table)
