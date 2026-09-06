# Callsign API (Unreleased)

The app and package version is 0.8.0. The additive callsign APIs below accompany
migration head `current_callsign_mentions`. Dashboard recordings and Archive
recording responses retain their existing fields.

## Resources and permissions

| Method and path | Permission | Result |
| --- | --- | --- |
| `GET /api/v1/callsigns` | Viewer | Directory page |
| `GET /api/v1/callsigns/{callsign}` | Viewer | Profile; 404 if unknown |
| `GET /api/v1/callsigns/{callsign}/mentions` | Viewer | Current mention page |
| `GET /api/v1/callsigns/last-heard?limit=1` | Viewer | Recent observations with cached QRZ enrichment |
| `PATCH /api/v1/callsign-mentions/{mention_id}` | Operator | Review result |
| `POST /api/v1/callsigns/{callsign}/qrz-refresh` | Operator | Updated profile |
| `PATCH /ui/callsign-mentions/{mention_id}` | Operator browser session | Review result |
| `POST /ui/callsigns/{callsign}/qrz-refresh` | Operator browser session | Updated profile |

Internet mode denies anonymous reads/writes with 401 and insufficient roles with
403. Operators include administrators. Cookie-authenticated writes require both
the session CSRF token and an Origin exactly equal to `ASLT_PUBLIC_BASE_URL`'s
origin. Missing/incorrect tokens and missing/disallowed origins return 403.
Machine bearer tokens use `/api/v1/`; `/ui/` requires browser sessions in internet
mode. Local trusted-network mode permits the UI's local administrator but API
writes still require authentication. No callsign response includes credentials,
QRZ session keys, or the absolute archive root.

## Directory and history pages

Pages contain `items`, `next_cursor` (opaque string or null), and `has_more`.
`limit` defaults to 50 and accepts 1–100. Pass `next_cursor` unchanged with the
same filters/sort; stop when `has_more` is false. Reset the cursor when changing
filters. Malformed cursors return HTTP 422 with
`{"detail":{"code":"invalid_cursor","message":"cursor must be valid"}}`.
Dated rows precede undated rows, with stable identity tie breakers. Pages traverse
a stable dataset exactly once; concurrent reviews or retranscription can change
membership, so restart the search to see a fresh view.

Directory filters: `q` (case-insensitive callsign substring), `alphabetical=true`
(default sorts last heard descending), `review_status`, and
`qrz_validation_status` (`found`, `not_found`). The normal directory excludes
rejected and superseded mentions; an explicit rejected directory filter therefore
returns no rows. Use the history rejected filter to retrieve rejected mentions.
QRZ-negative callsigns remain in the normal directory.

Each directory item contains `callsign`, nullable `qrz_display_name`,
`qrz_location`, `first_heard`, `last_heard`, `mention_count`, `recording_count`,
`active_days`, `confirmed_mentions`, `has_attributed_transmissions`, and nullable
`most_recent_confidence` (normalized overall mention confidence).

History filters: `from`, `to` (ISO dates or datetimes), `review_status`
(`detected`, `confirmed`, `corrected`, `rejected`), and `audio_status`
(`available`, `missing`, `expired`, `archived`, `protected`). A midnight `to` is
inclusive of that whole calendar date; other datetimes are inclusive instants.
Omitting review status excludes rejected rows. Explicit `review_status=rejected`
returns rejected **current** rows. QRZ validation filtering is on the directory,
not history. Unknown status values match no rows.

A history item contains `mention_id`, `recording_id`, `transcript_id`, nullable
`segment_id`, `heard_at`, `start_offset`, `end_offset`, `timing_precision`,
`raw_observed_value`, `canonical_callsign`, `recognition_method`,
`qrz_validation_status`, `confidence`, `acoustic_confidence`,
`recognition_confidence`, `evidence` (saved strings), `review_status`,
`audio_status`, `audio_available`, `recording_url`, and `excerpt` (at most 240
characters). `segment_avg_logprob` is an additive nullable raw Whisper score.
The three mention confidence fields are normalized values, not log probabilities.
Unknown values are null. Missing audio preserves history, evidence and timestamps.

Profiles contain callsign and cached `qrz_*` identity fields, first/last heard,
`total_mentions`, `unique_recordings`, `active_days`, four `*_mentions` review
counts, and a `confidence_summary` with nullable `minimum`, `average`, `maximum`.
Explicit attribution is separately reported as `attributed_transmission_count`,
`attributed_airtime_seconds`, `attribution_status` (`partial` or `unavailable`),
and `attribution_complete` (currently always false). Rejected mentions are counted
in their review bucket but excluded from normal totals/confidence. Confirming a
mention never assigns `Transmission.operator_callsign` or increases attribution.

## Review and refresh examples

Browser code runs on the configured public origin after OIDC sign-in; the browser
supplies Origin and the HttpOnly session cookie automatically:

```javascript
await fetch(`/ui/callsign-mentions/${mentionId}`, {
  method: 'PATCH',
  headers: {
    'Content-Type': 'application/json',
    'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content,
  },
  body: JSON.stringify({ action: 'correct', corrected_callsign: 'KM7GHS' }),
});
```

Machine clients use a named operator token created with the documented
[security CLI](security.md#machine-api-tokens):

```bash
curl -X PATCH "$BASE_URL/api/v1/callsign-mentions/$MENTION_ID" \
  -H "Authorization: Bearer $OPERATOR_TOKEN" -H 'Content-Type: application/json' \
  -d '{"action":"confirm"}'
curl -X POST "$BASE_URL/api/v1/callsigns/KM7GHS/qrz-refresh" \
  -H "Authorization: Bearer $OPERATOR_TOKEN"
```

Review actions are `confirm`, `reject`, or `correct`; only correction accepts and
requires `corrected_callsign`. Invalid requests return 422, unknown mentions 404.
The result contains `mention_id`, `canonical_callsign`, `review_status`,
`reviewer_identity`, and `reviewed_at`. Successful reviews write a security audit
with actor, operation, mention target, and before/after callsign and review status.

QRZ refresh validates the callsign before accessing the client. Success writes a
`callsign_qrz_refresh` audit with actor, callsign and resulting cache status.
Missing QRZ configuration returns 503; a QRZ failure returns a generic 502 without
upstream secrets. Failed/invalid refreshes do not write a successful refresh audit.
Authorization denials use the existing authorization audit. Automatic Last Heard
cache refreshes do not create manual-refresh audit records.

## Cache and evidence lifecycle

QRZ snapshots can be absent, `found`, or `not_found`. Lookup and expiration times
are stored with the canonical callsign. Unexpired snapshots need no network
lookup. Last Heard removes unexpired negative snapshots in SQL before its
1,000-candidate bound, so they cannot hide later eligible callsigns. Expired
negative snapshots are eligible for refresh. Per request,
`ASLT_QRZ_LAST_HEARD_REFRESH_LIMIT` bounds lookup attempts (including failures);
the first QRZ error stops further networking for that request. Unrefreshed/failed
expired entries are presented as unavailable, not newly validated. Manually
refreshing uses the QRZ client's normal lookup/cache behavior.

Only mentions with `is_current=true` on the recording's selected current
transcript enter normal directory/profile statistics, Last Heard, Archive
callsign filters and current history. An unmatched reviewed mention is retained
as `is_current=false`; there is no supported HTTP historical-review retrieval or
management UI. Database backups retain it for administrative inspection. A
profile for a canonical callsign can still exist with zero current mentions.
See [architecture](architecture.md#reviewed-evidence-lifecycle) for preservation
and purge semantics.
