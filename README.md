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
automatically in the background. With `ASLT_LIVE_TRANSCRIPTION=true`, a second
local loop snapshots a growing WAV through FFmpeg and publishes a provisional
rolling transcript while the radio is still keyed. The archive mount remains
read-only; snapshots are created below `ASLT_TMP_DIR` and deleted after each
pass. The completed recording always receives a new full-file accuracy pass.
Recordings are shown as `waiting` while their size or modification time is
changing, and as `live` after the first provisional result. They are queued for
the final pass only after size and modification time remain unchanged for
`ASLT_FILE_STABILIZATION_SECONDS`.

## AI transcription

Repeater Scribe uses a fully local, two-pass `faster-whisper` pipeline. While a
WAV is growing, FFmpeg extracts rolling 16 kHz tail snapshots for low-latency
provisional text. After the file is stable, the complete WAV receives a new
accuracy-oriented pass whose result is persisted. The two paths share one model
and serialize inference to stay within GPU memory.

The supplied profile targets a 12 GB NVIDIA card:

```dotenv
ASLT_WHISPER_MODEL=large-v3
ASLT_WHISPER_DEVICE=cuda
ASLT_WHISPER_COMPUTE_TYPE=float16
ASLT_WHISPER_BEAM_SIZE=5
ASLT_LIVE_TRANSCRIPTION=true
ASLT_LIVE_BEAM_SIZE=1
ASLT_LIVE_WINDOW_SECONDS=12
ASLT_LIVE_POLL_SECONDS=1.5
```

Model files are cached under `ASLT_WHISPER_MODEL_DIR`; after the initial model
download, audio and text remain local. There is no OpenAI transcription adapter
in the current application, and adding an API key does not activate one.

Benchmark the configured local final-pass model against real archive audio:

```bash
docker compose run --rm repeater-scribe \
  asl-transcriber benchmark /audio/YOUR_NODE_ID/example.wav
```

The JSON output includes audio duration, processing duration, real-time factor,
raw text, and callsign-corrected display text. A real-time factor below `1.0`
means inference completed faster than the recording duration.

Callsign handling is also local. Configured calls, favorites, active node data,
and topology calls form a ranked candidate set for phonetic decoding and
conservative correction. Prompt/hotword injection defaults off so candidate
lists cannot leak into provisional text. Both the untouched model output and
corrected display text are retained for completed recordings.

See [AI transcription](docs/transcription.md) for the exact live/final decode
settings, callsign algorithm, privacy boundary, CPU fallback, tuning procedure,
troubleshooting steps, and current limitations.

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
- `GET /api/v1/nodes/{home}/favorites` lists durable favorites, cached public-node statistics,
  and reported connection topology.
- `POST`/`PATCH`/`DELETE /api/v1/nodes/{home}/favorites` manage favorites with `X-API-Key` protection.

## AMI control

AMI is disabled by default. Set the AMI connection values in `.env`, then set
`ASLT_AMI_ENABLED=true`, `ASLT_AMI_CONTROL_ENABLED=true`, and a private
`ASLT_API_KEY` to enable node control. `ASLT_AMI_NODE_ID` has no default and must
be set to the operator's own node ID. Send the key in the `X-API-Key` header.
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

For public numeric favorites, the backend also polls the AllStarLink statistics
API while the favorite is disconnected. A persistent breadth-first crawler
follows public numeric downstream nodes to build the full observable connected
component, while caching node reports, crawl queues, metadata, and one- or
two-sided edges in the Docker-mounted database. Container restarts therefore
resume discovery instead of starting over. The dashboard uses that cache for
its Favorites table and dockable Network map, streams progressive graph updates,
merges live AMI state, keeps manually dragged bubble positions, and opens current
metadata and connection controls when a bubble is selected.

All AllStar traffic passes through one scheduler paced at one request every
three seconds (20/minute), below the service's discussed 30-request-per-minute
limit. Recent reports are reused across favorite crawls so a node is not fetched
twice, while favorite roots retain a 15-second priority refresh so activity and
key totals stay responsive during a long crawl. A crawl defaults to 200 nodes and 12 levels and clearly reports a safety
limit instead of silently claiming the graph is complete. Configure those bounds
with `ASLT_TOPOLOGY_MAX_NODES` and `ASLT_TOPOLOGY_MAX_DEPTH`; completed components
are revisited after `ASLT_TOPOLOGY_REFRESH_SECONDS` (15 minutes by default). Set
`ASLT_FAVORITE_STATS_ENABLED=false` to opt out; private nodes and nonnumeric
client identifiers continue to use locally observed AMI history only.

Processing loads the configured `faster-whisper` model on demand. The first
processing request may download the model and take longer than later requests.
See [AI transcription](docs/transcription.md) for the authoritative runtime
behavior and tuning guidance.

Node-control milestones after this foundation are favorites ordering, durable
transmission-to-recording correlation, AllStar/EchoLink station enrichment, and
time-zone-aware statistics presentation.
