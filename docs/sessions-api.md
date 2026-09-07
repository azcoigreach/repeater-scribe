# Sessions API (0.9.1)

The UI calls these resources **Events**. The existing `GET /api/v1/events` SSE
stream, its payloads and node-control APIs are unchanged.

All session reads require viewer authority. API mutations require an authenticated
operator or administrator. Named bearer tokens work on `/api/v1/sessions`.
Cookie writes require the session's `X-CSRF-Token` and exact public `Origin`.
The matching `/ui/sessions` paths follow existing browser permissions, including
trusted-local UI administration. An anonymous local principal cannot mutate the
machine API. Viewers cannot mutate either surface. Existing security middleware
records mutation audit events; domain rows retain available actor identities.

## Resources

| Method | Path beneath `/api/v1/sessions` | Purpose |
| --- | --- | --- |
| GET | `/sources` | Stable source IDs/labels (no filesystem roots) |
| GET | `` | Event page; filters `q`, `type`, `source_id`, `status`, `from`, `to`, `tag`, `recording_id`, `callsign` |
| POST | `` or `/start` | Create live or historical event; required `Idempotency-Key` |
| POST | `/preview` | Read-only membership count/sample for a creation payload |
| GET, PATCH | `/{id}` | Metadata, boundaries, source, state and metrics |
| POST | `/{id}/end` | End now or at supplied `ended_at` |
| POST | `/{id}/reopen` | Reopen saved event if source is free |
| GET | `/{id}/recordings` | Chronological recording/current transcript page |
| PATCH | `/{id}/recordings/{recording_id}` | `{"decision":"include"}`, `"exclude"` or `"automatic"` |
| PATCH | `/{id}/tags` | Replace event tags: `{"tags":["groovy","night"]}` |
| PATCH | `/{id}/recordings/{recording_id}/tags` | Replace catalog recording tags |
| GET, POST | `/{id}/markers` | Marker page/create |
| PATCH, DELETE | `/{id}/markers/{marker_id}` | Replace/edit or remove marker |
| GET | `/{id}/detected` | Current detected station aggregates with bounded evidence |
| GET, POST | `/{id}/checkins` | Confirmed roster page/create |
| PATCH, DELETE | `/{id}/checkins/{checkin_id}` | Replace/edit or undo check-in |

GET collections return `items`, `next_cursor` and `has_more`. `limit` defaults to
25, accepts 1–100. Pass the opaque cursor unchanged with the same filters;
restart pagination when changing them. Chronological collections sort by time
then stable UUID; detected callsigns use a canonical alphabetic cursor. Undated
manual recordings sort by catalog creation time for navigation only. Event dates
filter start times using `[from,to)`; overlapping historical events are allowed.
`callsign` filters confirmed attendance, while the separate detected endpoint
reports mention evidence.

Recordings default to included membership. Use `membership=decisions` to inspect
all explicit Include/Exclude decisions, including excluded recordings. Each row
includes `decision`, computed `automatic`, effective `included`, `boundary_crossing`,
`provisional`, tags, audio availability and the existing Archive transcript shape.
`latest=true` returns only the latest `limit` recordings in chronological order,
with `has_earlier`; do not combine it with a cursor. This supports a bounded live
view. Disable latest mode and use ordinary cursors for complete history. Polling
is sufficient for final transcription and review changes; no new SSE subscription
is required.

Event metadata contains `id`, `name`, `type`, `description`, `source_id`,
`source_label`, `started_at`, `ended_at`, derived `status`, `net_control`,
`created_at`, `updated_at`, `created_by`, `updated_by`. Details add tags,
recording count, elapsed seconds, included audio seconds, unknown audio duration
count and nullable station-attributed airtime. Source scope is immutable after
creation. PATCH only changes supplied fields; explicitly setting `ended_at:null`
reopens under the same active-source constraint. End must be strictly after start;
neither boundary may be future-dated. Timestamps must include a UTC offset.

Validation failures use HTTP 422, missing resources 404, permission failures
401/403 and conflicts 409, with the existing `detail` response convention.

## Start and end a net

First fetch `GET /api/v1/sessions/sources` and copy the selected ID. Illustrative
request (replace `SOURCE_ID` with the returned 64-character ID):

```http
POST /api/v1/sessions/start
Authorization: Bearer YOUR_OPERATOR_TOKEN
Idempotency-Key: groovy-2026-09-01-night
Content-Type: application/json

{
  "name": "Groovy Late Shift",
  "type": "Net",
  "source_id": "SOURCE_ID",
  "started_at": "2026-09-01T22:00:00-07:00",
  "net_control": "KM7GHS",
  "description": "Late shift check-ins",
  "tags": ["groovy", "night"]
}
```

Response, abbreviated (HTTP 200):

```json
{
  "id": "c0e81db1-4c23-4dca-8848-cceca285e43b",
  "name": "Groovy Late Shift",
  "type": "Net",
  "status": "active",
  "started_at": "2026-09-02T05:00:00Z",
  "ended_at": null,
  "recording_count": 0,
  "station_attributed_airtime_seconds": null
}
```

```http
POST /api/v1/sessions/c0e81db1-4c23-4dca-8848-cceca285e43b/end
Authorization: Bearer YOUR_OPERATOR_TOKEN
Content-Type: application/json

{"ended_at":"2026-09-01T23:00:00-07:00"}
```

The response is the saved event detail with `status:"ended"` and
`ended_at:"2026-09-02T06:00:00Z"`. Send `{}` to end at server time.

## Reconstruct from the Archive

```http
POST /api/v1/sessions
Authorization: Bearer YOUR_OPERATOR_TOKEN
Idempotency-Key: reconstruct-groovy-2026-08-31
Content-Type: application/json

{
  "name": "Groovy Late Shift — archive",
  "type": "Net",
  "source_id": "SOURCE_ID",
  "started_at": "2026-08-31T22:00:00-07:00",
  "ended_at": "2026-08-31T23:00:00-07:00",
  "tags": ["groovy", "historical"]
}
```

HTTP 200 returns an ended event, UTC boundaries and the matching recording count.
Preview the identical body through `/preview` to inspect `automatic_count`,
`explicit_inclusion_count`, `additional_automatic_count` and up to ten
`additional_examples`. For selected recordings, add `recording_ids:["UUID", ...]`
(maximum 100); these are durable explicit inclusions. All selected IDs must exist
in the chosen source. An open event's membership can grow after any preview.

## Markers and roster examples

```json
{
  "type": "Topic",
  "note": "Antenna discussion",
  "at": "2026-09-02T05:10:02Z",
  "callsign": "KM7GHS",
  "recording_id": "RECORDING_UUID",
  "audio_offset": 2
}
```

POST that body to `/{id}/markers`; omit recording/offset for an unanchored live
note. PATCH takes the same complete shape and retains creation identity/time.

```json
{
  "callsign": "KM7GHS",
  "at": "2026-09-02T05:10:02Z",
  "note": "Confirmed by ear",
  "recording_id": "RECORDING_UUID",
  "audio_offset": 2
}
```

POST to `/{id}/checkins`. `at` is claimed check-in time; `confirmed_at` and
`confirmed_by` describe the original confirmation. PATCH preserves those original
confirmation fields and updates `updated_at`/`updated_by`. Recording and offset
are optional. Duplicate canonical roster entries return 409. Markers and roster
rows expose `evidence_outside_event` when their saved recording is excluded, and
`audio_available`. Audio availability may change between response and playback;
use the existing authenticated Archive audio endpoint and handle 404/410.

## Retry and concurrency contract

- Creation/start requires `Idempotency-Key` (1–128 characters). It is persisted
  with authenticated subject and a fingerprint of normalized input in the same
  transaction as the event. Matching retries, including after restart or ending,
  return the same resource ID with its **current** state. An incompatible payload
  with the same subject/key returns 409. Keys do not expire automatically.
- Missing start means server time on the original successful creation. Retry with
  the same omitted value; do not replace it with a newly calculated start. Tags,
  selected recording IDs and timestamp offsets are normalized for comparison.
- End with `{}` is a no-op once ended. An explicit repeated identical end is also
  a no-op. A different end on an already ended event returns 409; use PATCH to
  intentionally correct a boundary. Reopen of an active event is a no-op. Reopen
  of an ended event conflicts if another event occupies that source.
- End/reopen are state-idempotent, not commands that can be replayed indefinitely
  across intervening opposite operations: an old end retried after a deliberate
  reopen ends the event again. Automations must serialize those transitions.
- SQLite write reservations serialize state-dependent mutations. A partial unique
  index independently enforces one open event per source; primary/unique keys
  prevent duplicate memberships, retry keys and roster entries across processes.
- Setting the same membership or tag list is safe. DELETE marker/check-in is a
  no-op if already absent from that existing event. Marker creation has no retry
  key; fetch the marker list before resubmitting after an uncertain response.
