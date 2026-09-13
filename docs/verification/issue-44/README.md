# Transcript action spacing verification

Issue: [#44](https://github.com/azcoigreach/repeater-scribe/issues/44).

Dashboard transcript cards now place Play audio and Re-transcribe in a scoped,
wrapping flex row with an 8px gap in both directions. The row retains the
previous 10px separation from metadata. Button styles, labels, event handlers,
permission checks, and transcript correction selectors remain unchanged.

## Visual verification

Screenshots use disposable browser fixtures with generated silent WAV audio,
UTC timezone, Chromium at 100% zoom, and a 1440 × 1000 viewport. The transcript
window is 640px or 260px wide and 700px tall. They contain no live archive data.

| State | Before | After |
| --- | --- | --- |
| Normal window, idle and disabled buttons | [Before](before-normal.png) | [After](after-normal.png) |
| Narrow window, wrapped buttons | [Before](before-narrow.png) | [After](after-narrow.png) |
| Playing after transcript refresh | [Before](before-playing-refreshed.png) | [After](after-playing-refreshed.png) |

[Playing in the narrow window](after-playing-narrow.png) also retains an 8px
gap: the shorter label fits alongside Re-transcribe, while Play audio wraps on
the disabled card below it. Visual inspection confirms readable labels and
consistent spacing across both cards.

The focused regression in the existing dashboard browser module checks actual
button bounds at both widths, including initial rendering, disabled audio,
disabled retranscription, real playback, refreshed transcript text, Queuing…,
and pending transcription after the retry response. The original implementation
failed the horizontal gap assertion with an observed 0px gap. The final check
allows the shorter Playing label to fit on one row in the narrow window.

## Local checks

Environment: existing `.venv`, Python 3.14.4, installed Chromium from
`/tmp/repeater-scribe-browsers`. CI uses the required Python 3.12 baseline.

| Exact command | Outcome |
| --- | --- |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/mypy src` | Passed; 39 source files |
| `.venv/bin/pytest -q -W error::DeprecationWarning` | 312 passed in 21.75s |
| `PLAYWRIGHT_BROWSERS_PATH=/tmp/repeater-scribe-browsers .venv/bin/pytest browser_tests -q -W error::DeprecationWarning` | 63 passed in 49.95s |
| `git diff --check` | Passed |

The Python and browser suites ran outside the execution sandbox with disposable
fixtures. Dependency auditing was not run locally and remains a CI check.
No migration, release, deployment, or live node/GPU/QRZ acceptance is needed for
this layout change.
