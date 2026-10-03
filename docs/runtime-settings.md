# Runtime Settings

Open **Settings** from the Dashboard menu. The left-side Dashboard and Appearance
tabs switch pages in place; Up/Down and Home/End navigate the tabs. Tab moves into
the selected page. Escape, Close, and Cancel discard unapplied edits and return
focus to the menu. Apply saves personal preferences and immediately refreshes
affected windows. Restore defaults fills in defaults for review before Apply.

| Preference | Supported range | Default |
| --- | --- | --- |
| Transcript display count | 1–500 whole recordings | 500 |
| Last Heard station display count | 1–100 whole stations | Server's effective `ASLT_QRZ_LAST_HEARD_LIMIT` (normally 25) |
| Appearance theme | `operator-dark` | Dark operator (the only implemented theme) |

The maxima match the existing API response caps and bound the number of cards
rendered. Counts apply to initial loading, search, periodic/event refresh, and
manual refresh. Fewer eligible results appear naturally. The Transcripts label
reports displayed versus matching recordings; queue database totals remain
independent. Last Heard's badge reports displayed stations. Ordering, eligibility,
QRZ cache/refresh budgets, archive history, ingestion, and retention do not change.
Active audio continues even when its recording leaves the visible results.

Preferences use browser local storage, scoped to the authenticated account's stable
principal subject (account ID for signed-in sessions), not its display name or
session token. They survive reload and sign-out/sign-in, but do not synchronize
across browsers or devices. Local unauthenticated mode uses the shared local-admin
scope. Local storage is personal presentation state, not a security boundary.
No preferences are copied from another account. Invalid, obsolete, or malformed
stored values fall back independently to valid defaults. If storage writes fail,
Apply explains the failure and does not change active preferences. Dashboard
layout/preset storage remains separate.

## Extending the implementation

`static/settings.js` owns the shell and `window.RuntimeSettings` registry. Load it
before page integrations. Stable keys are `dashboard.transcriptLimit`,
`dashboard.lastHeardLimit`, and `appearance.theme`; the storage namespace is
`repeater-scribe:preferences:v1:<principal subject>`.

- Use `registerSetting(key, definition)` for a personal setting. Numeric
  definitions provide `default`, `min`, `max`, `label`, and `help`; defaults and
  stored values use the same validation. The theme entry uses a `choices` allowlist
  and establishes the future theme entry point without showing a dummy selector.
- Use `registerPage({id, label, personal, settings, available, mount})` to add a
  page. `settings` lists numeric preference keys for generated controls. `mount`
  receives the page panel for custom content. Add new field types centrally in
  the registry if needed, rather than duplicating persistence or validation.
- `available()` omits inaccessible pages and their content. Server endpoints must
  independently authorize every request. Users (#43) and API Access (#56) should
  register their own pages here with `personal: false` (the default). Their
  runtime actions belong to those issues, not to personal Apply/Cancel/defaults.
- Read applied values with `get(key)`. Listen for `runtime-settings-change` on
  `window`; its `detail` lists only changed keys. Refresh only affected views using
  their existing rendering paths. Dialog navigation, modal focus, accessibility,
  and responsive layout are shared by every registered page.

Backend credentials, identity-provider configuration, connections, filesystem
paths, startup environment, and raw AllStar functions are outside this UI.
No database migration or service restart is required for preference changes.
