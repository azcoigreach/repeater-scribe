# Repeater Scribe

Read-only companion application for AllStarLink 3 archive recordings. It scans
WAV files, reads node-local `activity.log` files, and queues recordings for
local transcription without connecting to or controlling Asterisk.

## Live ASL3 archive

For a Dockerized ASL3 node, mount the host archive read-only and point the app
at the container path:

```yaml
volumes:
	- /home/azcoigreach/ASL3-Docker/asl_monitor:/audio:ro
	- ./data:/data
environment:
	ASLT_ARCHIVE_PATHS: /audio
	ASLT_DATABASE_URL: sqlite:////data/asl_transcriber.db
```

The application never renames, moves, deletes, or writes to files below the
configured archive paths. It scans on startup and polls the archive every five
seconds by default; adjust `ASLT_ARCHIVE_POLL_SECONDS` when needed.
With `ASLT_AUTO_PROCESS=true`, stable recordings are also transcribed
automatically in the background.
Recordings are shown as `waiting` while their size or modification time is
changing. They are queued only after an unchanged poll, so an active ASL3
recording is never processed mid-write.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check src tests
mypy src
```

Run a one-time scan with the configured archive paths:

```bash
ASLT_ARCHIVE_PATHS=/home/azcoigreach/ASL3-Docker/asl_monitor \
	asl-transcriber scan
```

Start the API with `uvicorn asl_transcriber.main:app --host 0.0.0.0 --port 8080`.

When running beside the AllScan Reimagined virtual-node stack, create or reuse
the external Docker network named by `ASL3_NETWORK_NAME` (default:
`asl3-docker_default`). This lets the AMI adapter resolve `allstarlink3`.

## API

- `GET /api/v1/health` reports service readiness.
- `POST /api/v1/ingestion/scan` performs a read-only archive scan.
- `POST /api/v1/ingestion/process` processes pending recordings with local Whisper.
- `GET /api/v1/ingestion/jobs` lists discovered recording jobs.
- `GET /api/v1/activity` lists parsed ASL3 activity events.
- `GET /api/v1/recordings?q=...&status=...` searches queued recordings and transcripts.
- `GET /api/v1/events` provides an SSE stream of discovery and processing events.
- `GET /api/v1/node/status` returns the shared app_rpt node-state cache without opening AMI.
- `POST /api/v1/node/ping` checks AMI connectivity.
- `POST /api/v1/node/{node_id}/function` sends an AllStar function code when AMI control and API-key protection are enabled.
- `GET /api/v1/node/{node_id}/commands` lists the named Functions menu.
- `POST /api/v1/node/{node_id}/command` executes a named command for API clients.
- `GET /api/v1/nodes` and `GET /api/v1/nodes/{home}/state` expose normalized app_rpt state.
- `GET /api/v1/nodes/{home}/links` and `GET /api/v1/nodes/{home}/events` expose live links and SSE updates.
- `POST`/`DELETE /api/v1/nodes/{home}/links` provide protected, named link controls.
- `GET /api/v1/nodes/{home}/favorites` lists durable favorites and their key-up totals.
- `POST`/`PATCH`/`DELETE /api/v1/nodes/{home}/favorites` manage favorites with `X-API-Key` protection.

## AMI control

AMI is disabled by default. Set the AMI connection values in `.env`, then set
`ASLT_AMI_ENABLED=true`, `ASLT_AMI_CONTROL_ENABLED=true`, and a private
`ASLT_API_KEY` to enable node control. Send the key in the `X-API-Key` header.
Control requests are limited to AllStar DTMF function codes; arbitrary AMI
actions are not exposed by the HTTP API.
The container owns one persistent AMI connection per configured home node. It
logs in with events enabled, preserves repeated headers, routes actions by
unique `ActionID`, and refreshes app_rpt state after authentication and
reconnect. `RptStatus XStat` is authoritative for direct links and is joined to
`SawStat` for key-up timing. Older app_rpt installations fall back to adjacent
links from `RPT_ALINKS`; `activity.log` is never used as current link state.
The backend repairs its cache every five seconds and publishes changes to all
browsers over one SSE path, so additional dashboard sessions do not create AMI
connections or add app_rpt polling load.
The dashboard uses the server-configured AMI credentials and does not ask the
operator to enter the API key. Its command drawer is available only when web
authentication is explicitly off; enable authentication before exposing the
dashboard beyond a trusted local network.

Favorite nodes are stored in the same Docker-mounted database configured by
`ASLT_DATABASE_URL`, including their callsign, description, and location. The
backend counts observed remote key-up transitions for every direct node link,
so existing history is available if a node is favorited later. Counts and
transmit time begin accumulating after this version is deployed; they are not
reconstructed from historical recordings.

Processing loads the configured `faster-whisper` model on demand. The first
processing request may download the model and take longer than later requests.

Node-control milestones after this foundation are favorites ordering, durable
transmission-to-recording correlation, AllStar/EchoLink station enrichment, and
time-zone-aware statistics presentation.
