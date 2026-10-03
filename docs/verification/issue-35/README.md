# Issue #35 Settings verification

The screenshots use the isolated browser acceptance server, generated audio,
disposable database, fixture accounts, and stubbed external services.

- [Desktop, 1440 × 850](desktop.png)
- [Narrow window, 375 × 850](narrow.png)

Both show the left-side navigation, labeled count inputs, range/default helper
text, account/browser persistence scope, and Apply/Cancel/Restore defaults.
The narrow layout retains vertical navigation and scrolls when needed.

Reproduce screenshots with:

```bash
ASLT_SETTINGS_SCREENSHOTS=/tmp/issue35-screenshots \
  pytest browser_tests/test_settings_browser.py -q -W error::DeprecationWarning
```

The acceptance suite covers both count directions, fewer available records,
unchanged database totals/layout, search, manual and timed refresh, reload,
account isolation, stale response ordering, malformed storage/input, failed
storage writes, Apply/Cancel/defaults, page permissions/action boundaries,
keyboard navigation/focus, and uninterrupted audio with retained station evidence.
API tests exercise authenticated limit overrides, bounds, unique stations, and
unchanged QRZ lookup budgets. Existing eligibility/cache regression tests also run.
A deterministic one-worker regression verifies that closing/reloading Dashboard
event streams releases their idle queue reads, keeping authenticated Apply/refresh
requests responsive. Final browser verification also uses the repository-pinned
Caddy binary via `ASLT_TEST_CADDY_BINARY`.

No migration or live ASL3/AMI/GPU/QRZ acceptance is needed for personal presentation
preferences. Automated tests never issue live node commands or access a live
archive. See the linked PR for exact final check results and CI status.
