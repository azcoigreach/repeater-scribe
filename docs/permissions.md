# Endpoint permission matrix

Minimum authority is cumulative: Admin includes User and Viewer; User includes
Viewer. `operator` survives only as an internal/configuration/CLI migration alias
for `user`. This matrix covers all application endpoints, including UI aliases.

Cookie-authenticated shared-data mutations and logout require CSRF and exact
public Origin. `/ui/` writes require browser sessions in internet mode. `/api/`
writes accept named tokens or protected browser sessions and reject implicit local
Admin access. `POST .../sessions/preview` only reads: it requires Viewer and does
not mutate account or shared state. Workspace pages redirect signed-out visitors
to login; protected APIs return 401 (missing credential) or 403 (insufficient role).

AMI availability, control enablement, raw-function enablement, command validation,
confirmation requirements, filesystem bounds, and resource validity are additional
checks after role authorization. See [account behavior and recovery](accounts.md).

| Method | Endpoint | Minimum role |
| --- | --- | --- |
| GET | `/` | Viewer |
| GET | `/api/v1/accounts` | Admin |
| GET | `/api/v1/accounts/{account_id}` | Admin |
| PATCH | `/api/v1/accounts/{account_id}` | Admin |
| GET | `/api/v1/activity` | Viewer |
| GET | `/api/v1/archive/recordings` | Viewer |
| GET | `/api/v1/archive/recordings/{recording_id}` | Viewer |
| GET | `/api/v1/archive/recordings/{recording_id}/audio` | Viewer |
| GET | `/api/v1/audio` | Viewer |
| GET | `/api/v1/auth/me` | Viewer |
| PATCH | `/api/v1/callsign-mentions/{mention_id}` | User |
| GET | `/api/v1/callsigns` | Viewer |
| GET | `/api/v1/callsigns/last-heard` | Viewer |
| GET | `/api/v1/callsigns/{callsign}` | Viewer |
| GET | `/api/v1/callsigns/{callsign}/mentions` | Viewer |
| POST | `/api/v1/callsigns/{callsign}/qrz-refresh` | User |
| GET | `/api/v1/events` | Viewer |
| GET | `/api/v1/health` | Public |
| GET | `/api/v1/ingestion/jobs` | Admin |
| POST | `/api/v1/ingestion/jobs/{job_id}/callsign-correction` | User |
| POST | `/api/v1/ingestion/jobs/{job_id}/retry` | User |
| POST | `/api/v1/ingestion/process` | Admin |
| POST | `/api/v1/ingestion/scan` | Admin |
| POST | `/api/v1/node/ping` | User |
| GET | `/api/v1/node/status` | Viewer |
| POST | `/api/v1/node/{node_id}/command` | User |
| GET | `/api/v1/node/{node_id}/commands` | Viewer |
| POST | `/api/v1/node/{node_id}/function` | User |
| GET | `/api/v1/nodes` | Viewer |
| GET | `/api/v1/nodes/{home}/events` | Viewer |
| GET | `/api/v1/nodes/{home}/favorites` | Viewer |
| POST | `/api/v1/nodes/{home}/favorites` | User |
| DELETE | `/api/v1/nodes/{home}/favorites/{favorite_id}` | User |
| PATCH | `/api/v1/nodes/{home}/favorites/{favorite_id}` | User |
| DELETE | `/api/v1/nodes/{home}/links` | User |
| GET | `/api/v1/nodes/{home}/links` | Viewer |
| POST | `/api/v1/nodes/{home}/links` | User |
| DELETE | `/api/v1/nodes/{home}/links/{target}` | User |
| POST | `/api/v1/nodes/{home}/reconnect` | User |
| GET | `/api/v1/nodes/{home}/state` | Viewer |
| GET | `/api/v1/nodes/{home}/topology` | Viewer |
| GET | `/api/v1/nodes/{home}/topology/events` | Viewer |
| GET | `/api/v1/recordings` | Viewer |
| GET | `/api/v1/sessions` | Viewer |
| POST | `/api/v1/sessions` | User |
| POST | `/api/v1/sessions/preview` | Viewer |
| GET | `/api/v1/sessions/sources` | Viewer |
| POST | `/api/v1/sessions/start` | User |
| GET | `/api/v1/sessions/{session_id}` | Viewer |
| PATCH | `/api/v1/sessions/{session_id}` | User |
| GET | `/api/v1/sessions/{session_id}/checkins` | Viewer |
| POST | `/api/v1/sessions/{session_id}/checkins` | User |
| DELETE | `/api/v1/sessions/{session_id}/checkins/{checkin_id}` | User |
| PATCH | `/api/v1/sessions/{session_id}/checkins/{checkin_id}` | User |
| GET | `/api/v1/sessions/{session_id}/detected` | Viewer |
| POST | `/api/v1/sessions/{session_id}/end` | User |
| GET | `/api/v1/sessions/{session_id}/markers` | Viewer |
| POST | `/api/v1/sessions/{session_id}/markers` | User |
| DELETE | `/api/v1/sessions/{session_id}/markers/{marker_id}` | User |
| PATCH | `/api/v1/sessions/{session_id}/markers/{marker_id}` | User |
| GET | `/api/v1/sessions/{session_id}/recordings` | Viewer |
| PATCH | `/api/v1/sessions/{session_id}/recordings/{recording_id}` | User |
| PATCH | `/api/v1/sessions/{session_id}/recordings/{recording_id}/tags` | User |
| POST | `/api/v1/sessions/{session_id}/reopen` | User |
| PATCH | `/api/v1/sessions/{session_id}/tags` | User |
| GET | `/api/v1/system/info` | Admin |
| GET | `/archive` | Viewer |
| GET | `/archive/recordings/{recording_id}` | Viewer |
| GET | `/auth/callback` | Public |
| GET | `/auth/login` | Public |
| POST | `/auth/logout` | Viewer |
| GET | `/callsigns` | Viewer |
| GET | `/callsigns/{callsign}` | Viewer |
| GET | `/events` | Viewer |
| GET | `/events/{session_id}` | Viewer |
| GET | `/health` | Public |
| PATCH | `/ui/callsign-mentions/{mention_id}` | User |
| POST | `/ui/callsigns/{callsign}/qrz-refresh` | User |
| POST | `/ui/ingestion/jobs/{job_id}/callsign-correction` | User |
| POST | `/ui/ingestion/jobs/{job_id}/retry` | User |
| POST | `/ui/node/{node_id}/command` | User |
| POST | `/ui/node/{node_id}/function` | User |
| POST | `/ui/nodes/{home}/favorites` | User |
| DELETE | `/ui/nodes/{home}/favorites/{favorite_id}` | User |
| PATCH | `/ui/nodes/{home}/favorites/{favorite_id}` | User |
| POST | `/ui/nodes/{home}/topology/{root}/crawl` | User |
| GET | `/ui/sessions` | Viewer |
| POST | `/ui/sessions` | User |
| POST | `/ui/sessions/preview` | Viewer |
| GET | `/ui/sessions/sources` | Viewer |
| POST | `/ui/sessions/start` | User |
| GET | `/ui/sessions/{session_id}` | Viewer |
| PATCH | `/ui/sessions/{session_id}` | User |
| GET | `/ui/sessions/{session_id}/checkins` | Viewer |
| POST | `/ui/sessions/{session_id}/checkins` | User |
| DELETE | `/ui/sessions/{session_id}/checkins/{checkin_id}` | User |
| PATCH | `/ui/sessions/{session_id}/checkins/{checkin_id}` | User |
| GET | `/ui/sessions/{session_id}/detected` | Viewer |
| POST | `/ui/sessions/{session_id}/end` | User |
| GET | `/ui/sessions/{session_id}/markers` | Viewer |
| POST | `/ui/sessions/{session_id}/markers` | User |
| DELETE | `/ui/sessions/{session_id}/markers/{marker_id}` | User |
| PATCH | `/ui/sessions/{session_id}/markers/{marker_id}` | User |
| GET | `/ui/sessions/{session_id}/recordings` | Viewer |
| PATCH | `/ui/sessions/{session_id}/recordings/{recording_id}` | User |
| PATCH | `/ui/sessions/{session_id}/recordings/{recording_id}/tags` | User |
| POST | `/ui/sessions/{session_id}/reopen` | User |
| PATCH | `/ui/sessions/{session_id}/tags` | User |

`/static/*` exposes public application assets. Local-mode `/docs`, `/redoc` and
`/openapi.json` are public developer tools; all three are disabled in internet
mode. Framework OPTIONS/HEAD behavior adds no write authority. The current health
response includes version only in local mode. No account/session-management UI
or personal-token endpoint is included in this change.
