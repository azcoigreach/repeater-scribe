# Callsign directory heading verification

Issue: [#32](https://github.com/azcoigreach/repeater-scribe/issues/32).

The directory heading now renders at 24px (1.5rem), weight 700, with a 30px
line height, compared with the previous 10.53px heading and unchanged 16px
supporting details. The CSS is scoped to `#callsign-directory`; shared compact
links retain their 9px size, themed color, hover treatment, and focus outline.
No API, data, or product version changes are involved.

## Visual comparison

Screenshots use the same disposable browser fixtures, UTC timezone, Chromium,
100% zoom, and viewport height of 1000px. The literal HTML-like operator name is
intentional hostile-text test data, rendered safely as text. No live station or
account data was used.

| Viewport | Before | After |
| --- | --- | --- |
| Desktop, 1440px | [Before](before-desktop.png) | [After](after-desktop.png) |
| Mobile, 390px | [Before](before-mobile.png) | [After](after-mobile.png) |

Visual inspection confirms that callsigns are the most prominent text in each
card while the existing page layout and supporting information remain readable.
Automated geometry checks cover every loaded heading at 1440px, 390px, and
320px, plus 200% CSS zoom at 1440px, with no heading clipping, overlap with
supporting text, or horizontal document overflow. CSS zoom provides automated
magnification coverage; native browser zoom controls were not separately tested.

Browser regression coverage verifies:

- At least 24px bold callsigns, at least 1.5 times the supporting text size,
  using the established font after initial loading, Load more, sort, and search.
- The themed blue link, contrasting underlined hover, visible 2px keyboard
  focus outline, and Enter navigation to the linked station's detail page.
- Compact callsign evidence links on the Archive page still render at 9px.

## Local checks

Environment: existing `.venv`, Python 3.14.4, installed Chromium from
`/tmp/repeater-scribe-browsers`. CI uses the required Python 3.12 baseline.

| Exact command | Outcome |
| --- | --- |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/mypy src` | Passed; 39 source files |
| `.venv/bin/pytest -q -W error::DeprecationWarning` | 312 passed in 21.19s |
| `PLAYWRIGHT_BROWSERS_PATH=/tmp/repeater-scribe-browsers .venv/bin/pytest browser_tests -q -W error::DeprecationWarning` | 61 passed in 47.92s |
| `git diff --check` | Passed |

The completed Python and browser suites ran outside the execution sandbox using
disposable fixtures. An initial sandboxed Python run was interrupted after early
failures; sandboxed Chromium could not launch. Both suites subsequently passed
in full. Dependency auditing was not run locally and remains a CI check.
