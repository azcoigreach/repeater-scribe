# Events, nets and sessions (0.9.0)

Use **Events** to save a named radio activity and return to its recordings,
transcript, markers and confirmed roster later. The internal resource name is
`session`; `/api/v1/events` continues to serve the existing live SSE stream.

## Run Groovy Late Shift live

1. Choose **Start Event** on the dashboard or Events workspace. Enter
   **Groovy Late Shift**, select **Net** and the monitored source. Optionally
   enter net-control callsign, description and comma-separated tags.
2. Leave the end blank. The start defaults to now and can be corrected backward.
   Start the event. Only one event may be active for each monitored source.
3. Recordings accumulate from the durable catalog. Active events follow the
   latest 25 recordings, displayed chronologically. Turn off **Follow latest**
   to browse the full history with **Load more recordings**. The visible page,
   event metrics, detected callsigns and roster refresh every five seconds when
   the browser tab is visible. Existing audio playback is retained during updates.
4. Add a marker with an absolute time and note, or choose **Mark audio position**
   on a recording. Review the separate **Detected callsigns** panel. **Confirm
   Check-In** fills the roster form; review the claimed time and submit it to
   confirm attendance. Use the same form for a missed callsign.
5. **End Event** from the detail view or dashboard. All history remains saved.
   **Reopen Event** resumes the same saved event if its source has no active
   event; existing markers, manual decisions and attendance remain intact.

Types: Net, Exercise, Club Event, POTA, Testing, Maintenance, Roundtable,
Special Event Station, QSO Session and Custom. Types classify the event and do
not change membership or grant permissions.

## Reconstruct an event

In **Archive**, choose the monitored source and From/To date and time, then
**Create event from range**. Name the event, review **Preview membership**, and
save. Other Archive filters (text, callsign, status or tags) do not constrain
an event window: the preview includes every overlapping recording for the
selected source. Archive's existing To search filter is inclusive; event windows
use an exclusive end, so a recording beginning exactly at To is not an automatic
member. Boundary-crossing recordings may start before the Archive From filter.

Alternatively, select recording checkboxes and choose **Create event from
selected**. Select at most 100 recordings from one source. The event starts with
the selected range, but the selections themselves become explicit inclusions.
The preview shows the number and up to ten examples of additional automatic
members. Unknown-time selections remain explicit inclusions; enter appropriate
event boundaries yourself. Changing boundaries never removes an explicit inclusion.

On the event detail page, edit metadata, start/end boundaries and tags. Use
**Include**, **Exclude** or **Automatic** on recordings. To restore an excluded
recording, switch Membership view to **Manual decisions**, then choose Include
or Automatic. You can also include a recording by its ID from the Archive detail
URL. Manual inclusion requires the same monitored source.

## Membership contract

- A source is the catalog's archive-root identity, shown with a short stable ID
  to distinguish roots with the same basename. Node identifiers are not reliably
  populated for legacy recordings and are not used as a substitute source scope.
- Windows are half-open: `[start, end)`, or `[start, infinity)` while active.
- A positive known-duration recording is automatic when its recorded interval
  overlaps the window. Unknown/zero-duration recordings use their start instant.
- Only `Recording.started_at`, derived from the ASL filename, is trusted for
  automatic membership. Missing timestamps require explicit inclusion; catalog
  creation time, filesystem modification time and transcription completion time
  never substitute for recorded time.
- Crossing audio is included **whole** and labeled. Neither audio nor transcript
  is trimmed. The source archive is never modified.
- Automatic membership is stored separately from an Include/Exclude override.
  Include persists through boundary edits; Exclude prevents re-addition;
  Automatic clears the override. A recording can belong to several events.
- Recorded timestamp/duration/source updates reconcile affected events in the
  same catalog transaction. This includes late discovery after an event ended.
  Startup reconciles saved active and ended events, using bounded batches.
  Ongoing source refresh probes changed durations through the existing catalog
  refresh cycle. Delayed transcription affects displayed text, not membership.
- Missing/expired audio preserves recordings, transcripts, membership, markers
  and attendance. Playback reports availability while retained evidence remains
  readable. Reappearing available audio can be played again.

## Markers and attendance

Marker types include Net Started, Check-In, Topic, Emergency Traffic, Net Closed
and Custom. The note can describe an observation or topic. An audio-linked
marker references recording ID plus seconds from its beginning; segment IDs can
change during retranscription. Offsets must be nonnegative and cannot exceed a
known duration. Click its playback link to open the Archive detail at that offset.

A marker without a recording retains its absolute UTC time. Reconciliation
anchors it only if exactly one included recording with a known positive duration
contains that instant. Ambiguous markers remain unanchored. You can edit the
anchor manually. A saved anchor is never silently moved to another recording.
Markers outside changed boundaries and evidence whose recording is subsequently
excluded remain visible, with an evidence warning.

Detected callsigns come exclusively from existing current-transcript,
non-rejected, current callsign mentions. Counts are **mentions** and **recordings**,
not transmitting stations or check-ins. First/last mention times and up to three
supporting links are shown; station history provides further evidence. Reviewed
spelling and QRZ validation do not confirm attendance.

Confirmed check-ins are independent operator records. Edit the claimed check-in
time, note and optional recording/offset; the original confirming actor and time
remain separate. **Undo check-in** removes an incorrect roster entry. The existing
callsign normalizer defines uniqueness, including its portable/base-callsign
conventions (for example `KM7GHS/P` maps to `KM7GHS`). Repeated or concurrent
confirmations cannot create duplicate canonical entries. Retranscription,
rejected/disappearing mentions and excluded recordings do not remove attendance.
A supporting recording that is no longer included is explicitly flagged.

## Time and metrics

Inputs and display follow the existing Archive convention: **browser local
timezone**, named beside the event form. UTC is stored in SQLite; APIs accept
ISO 8601 timestamps with `Z` or an explicit offset and return UTC. The existing
`ASLT_SOURCE_TIMEZONE` configuration field is not applied by the current parser
or browser; the established catalog/activity timestamp convention is UTC. This
release does not reinterpret existing filename timestamps.
Use an explicit offset through the API to disambiguate a repeated DST hour.

**Event elapsed** is wall-clock end minus start (now minus start while active).
**Included recording audio** sums whole recording durations; unknown-duration
counts are displayed separately. Overlap and crossing audio can make this total
larger than elapsed time. **Station-attributed airtime** uses only existing
explicitly attributed transmissions attached to included recordings and is
partial evidence, not a complete air-time accounting. Unsupported counts/time are
unavailable, never invented zeros. Current saved transcript revisions take
precedence; a live provisional transcript appears only when no saved revision
exists and is labeled provisional.

See [API and retry contract](sessions-api.md) and [upgrade instructions](upgrade-0.9.md).
