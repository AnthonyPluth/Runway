# Scripted flows

Each `*.json` file here is a flow that `make verify` runs besides visiting the pages (each saved as a full page, `<page>-<width>.png`, and as its first screen, `<page>-top-<width>.png`), at phone (440×956, an iPhone 17 Pro Max) and desktop (1280×800) widths (or the `viewports` it lists). There is no tablet width. A step that fails saves a `-FAILED-` image to `artifacts/verify/`, which is never committed. A pull request adds steps by adding or editing a file.

```json
{
  "name": "categories-emoji",
  "page": "setup",
  "viewports": ["phone", "desktop"],
  "steps": [
    { "goto": "#setup/categories" },
    { "click": "button[aria-label^='Emoji for ']" },
    { "wait_for": "[role=dialog]" },
    { "screenshot": "picker-open" }
  ]
}
```

- `page` is the route the flow starts on (default `overview`); `viewports` defaults to `phone` and `desktop`; `landscape` (844×390, touch: a phone turned sideways) runs only when a flow lists it.
- `"signed_in": true` runs the flow signed in: against a demo server with sign-in on (runway/verify.py's `serve`), with a session made for it and a virtual platform authenticator (Chrome's DevTools WebAuthn) standing in for Face ID or Touch ID. The app lock's flow (`app-lock.json`) needs it. Each viewport is a new browser on the same session, so a flow that changes something about the session puts it back at the end.
- A step has one action, and an optional `timeout` in milliseconds (default 10000). Selectors are [Playwright selectors](https://playwright.dev/docs/other-locators) (CSS, `text=…`, `role=…`).
  - `{"goto": "#route"}` opens a route of the app (or a full URL).
  - `{"click": selector}`, `{"fill": {"selector": …, "text": …}}`, `{"press": {"selector": …, "key": "Enter"}}`. A click first waits for a dialog or sheet the step before closed to let go of the page: until it has, the click lands on the closing dialog, and Playwright's retries scroll the page a different way each time.
  - `{"scroll_to": selector}` scrolls it to the middle of the window (and, inside a table that scrolls sideways, to its left edge), so the screenshot after it shows it.
  - `{"wait_for": selector}` waits for it to appear; `{"expect_text": {"selector": …, "text": …}}` fails unless it contains the text.
    `expect_text` reads the text once the selector exists and does not wait for the text to arrive: for content a page loads after it renders (a `{#await}` block), `wait_for` a `text=…` selector first, or a slow runner reads the page before it is filled in.
  - `{"expect_above": {"selector": …, "above": …}}` fails unless the first match is whole inside the window and ends above the first match of `above` (the floating navigation, say): that is, it is on the first screen.
  - `{"reload": true}` opens the app again (a launch), not just another route.
  - `{"authenticator": "verified"}` or `"unverified"` (signed-in flows only): the virtual authenticator's Face ID works, or fails.
  - Keyboard: `{"tab": {"count": 16, "shift": false, "stay_in": "[role=dialog]"}}` presses Tab (Shift+Tab with `shift`) `count` times and fails if focus ever leaves `stay_in`; `{"expect_focus": selector}` fails unless the focused element matches it; `{"expect_inert": selector}` fails unless it is inert (itself or an ancestor).
  - `{"screenshot": "name"}` saves the full page (the window, when a dialog, a sheet or the To review bar is showing) as `flow-<flow name>-<name>-<viewport>.png`.
- A flow also ends with a screenshot, and fails the run on a failed step, a console error or a 5xx response.
- A flow is also audited for accessibility where it ends (axe, focus rings, and tap targets at phone width: `frontend/verify/a11y.mjs`), at each width it lists, so a sheet or dialog the flow leaves open is checked too. A finding not in `frontend/verify/a11y-baseline.txt` fails the flow.
- A screenshot waits until nothing on the page is still loading (no skeleton, nothing `aria-busy`, no transition moving), and is retaken until two captures in a row match; one that never settles fails the flow. The `expect_text` and `expect_focus` steps since the last action are checked again after it, so a panel that closes while it's captured fails instead of being kept.
- A step's `timeout` is for an idle machine: on a busy one every limit stretches with the load. A flow that fails is run again on fresh demo data (three tries in all).
- **Flows are independent.** Every flow (and every page) runs on a demo server and a fresh copy of the demo data of its own, several at once (`VERIFY_JOBS`), in no fixed order. A flow starts from the demo data as `make verify` seeds it, never from what another flow did, so it must not depend on another flow's data, and its images are the same in a full run, a partial one (`PAGES="<flow>"`), any order and any number of workers (`make verify-twice` checks the last two). A flow that needs something the demo data doesn't have gets it from the seed (`runway/domain/demo.py`, through an option `make verify` passes to `run.py demo`, like `--receipt`) or from its own steps. Its widths run one after the other on the same data, so what it changes at one width is there at the next.
- A flow's name (its file's) picks it alone: `make verify PAGES="categories-emoji"`. It can't be a page's name.

Flows run against the demo data only.
