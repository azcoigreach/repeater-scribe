# Upgrade to 0.9.1

From 0.9.0, rebuild and restart with the 0.9.1 code. This patch adds no
database migration: the required head remains `events_sessions`. UTC storage
and container timezone settings stay the same. The UI follows the browser’s
local timezone; Arizona users should use `America/Phoenix`. All UI script and
stylesheet URLs carry the 0.9.1 version to refresh cached assets.

The steps below also cover upgrades from 0.8.1, which require the Events migration.

The verified baseline is 0.8.1 at `c7f98ef`, with Alembic revision
`transcript_text_corrections`. Some earlier callsign documents still reported
0.8.0; package/application metadata and repository history established 0.8.1 as
the actual baseline. The 0.9.0 migration head is `events_sessions`.

1. Back up the configured SQLite catalog using `asl-transcriber backup-db
   /path/to/backup.db` and verify it with `asl-transcriber verify-db
   /path/to/backup.db`. Preserve the existing data volume.
2. Build the application image from the updated checkout **before** running
   Alembic, so the migration container includes the new migration files:

   ```bash
   docker compose build repeater-scribe
   ```

   `docker compose run` can reuse an older image. Building only when starting
   the service afterward is too late for the preceding migration command.
3. Stop application traffic/background workers, then migrate using the newly
   built image and the same database configuration:

   ```bash
   docker compose stop repeater-scribe
   docker compose run --rm repeater-scribe alembic upgrade head
   docker compose run --rm repeater-scribe alembic current
   ```

   For a non-container installation, install the updated code, stop the workers,
   and run `alembic upgrade head` followed by `alembic current` in its environment.
4. Start the image that was built and migrated:

   ```bash
   docker compose up -d --no-build --force-recreate repeater-scribe
   ```

   Startup requires `events_sessions` and refuses an outdated catalog.
   The runtime recovers active events and reconciles saved event associations,
   including ended events and recordings discovered while the service was down.
5. Open Events and verify source selection, Archive access and the active-event
   indicator. Source identity follows the existing absolute archive root: keep
   container mount paths stable through the upgrade.

Use the same Compose profile for every command. For internet installations,
replace `docker compose` above with
`docker compose -f docker-compose.yml -f compose.internet.yml`.

The migration adds `radio_sessions`, `session_requests`, `session_recordings`,
`session_markers`, `session_checkins`, `tags`, `session_tags`, `recording_tags`,
foreign keys, constraints and source/window lookup indexes. It does not rebuild
or delete existing transcript, recording, segment, mention, correction, review,
FTS or node-control data. A fresh installation still runs the complete migration
lineage. No database, task queue, hosted service or frontend framework is added.

Regular source-audio rotation remains independent of the catalog; never delete
source audio as an upgrade step. There is no automatic attendance backfill from
old mentions. Start or reconstruct events explicitly.

Downgrading the schema removes the new event data. To roll back without losing
saved events, retain a complete post-upgrade backup as well as the pre-upgrade
backup, stop traffic, and restore the appropriate catalog/code pair. Do not run
older application code against a newer schema.

The implementation task does not perform any of these deployment steps against
your running installation and does not publish a release or create a release tag.

See [0.9.1 verification](verification-0.9.1.md) and
[the original Events verification report](verification-0.9.md) for migration and
browser acceptance coverage and environment limits.
