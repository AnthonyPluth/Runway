# Gotchas

What usually goes wrong when driving Runway with `tools/control`, and what to do about it.

## The clock is frozen

- The server and the seed run under libfaketime at `FROZEN_DAY` in `runway/verify.py` (2026-06-17, noon), and the browser's clock is fixed at the same moment. "Today" on every page is that day, whatever the real date is, and dates, due dates and chart axes never move.
- Without libfaketime nothing starts. `doctor` says so and prints the install line (`sudo apt-get install -y libfaketime`). It works on Linux only, because macOS has no `LD_PRELOAD`.
- A time of day the app shows ("as of 12:00:00 PM") is frozen too. A bug that only happens as time passes (a timer, a 72-hour window) can't be reproduced here: write a test with fake timers.

## Flows are independent

- Each make verify page and flow runs on its own server and its own fresh copy of the demo data. A flow starts from the seed, never from what another flow did.
- `tools/control flow <name>` runs on your session's data. If you changed something, run `reset` first to get the data make verify would start it from.
- A flow marked `signed_in` needs `start --signed-in`. A flow's images go to `artifacts/control/flows/`, named as make verify names them.

## Sizes

- Phone is 440×956 (an iPhone 17 Pro Max) and desktop is 1280×800, the only two widths make verify shoots. A layout change is checked at both: `viewport phone`, `screenshot`, `viewport desktop`, `screenshot`.
- There is no tablet width. For the 768 to 1023 px rail layout, resize a real browser by hand. Nothing from that gets committed.
- A full-page screenshot shows fixed parts (the phone's tab bar, a sticky header) where the window was, which can be part way down the page. make verify's images look the same way. Use `screenshot --viewport` to see what a person sees on one screen.

## The theme

- make verify, and so this session, shows the dark theme only. For a change that depends on the theme, check the light theme by hand and say so in the PR.

## Settling and flakes

- `screenshot`, `goto` and `wait-settle` wait for make verify's readiness rule: no skeleton, nothing `aria-busy`, no transition running, live prices in, fonts and the document loaded. A page that never settles fails with what was still loading, and that is a bug.
- Before a capture, the page's port numbers (the MCP address) are rewritten to 8080, the same as make verify does, so an input that shows the address changes.
- Raster flakes are rare: an edge or a rounded corner that comes out one or two levels apart between runs. Take the shot again before you believe a one-pixel difference.
- Hover state is cleared before a capture (the pointer moves to 0,0). On the phone, row buttons that appear on hover (such as Delete) aren't visible, so `click` doesn't find them. Use the desktop width, or the row's details.

## One verify per machine

- make verify holds a machine-wide lock (`/tmp/runway-verify.lock`), so a second run waits. A control session never takes that lock and uses its own ports and data, so it doesn't break a run, but it does add load. `start` refuses a busy machine unless you pass `--anyway`.
- There's one session per checkout. A second `start` says one is running. `stop` it, or keep using it.
- A session stops by itself after 30 idle minutes (`--idle-minutes`). If a command then reports `stale_session` or `no_session`, run `start` again.

## Targets

- Use the names `snapshot --text` shows: `click button "Save"` is `click button Save`. Text matching ignores case and matches substrings; add `--exact` for an exact match.
- More than one visible match is an error that lists them. Pick one with `--nth`, or name it more exactly.
- Text that starts with `-` goes after `--`: `type textbox Amount -- -5`.
