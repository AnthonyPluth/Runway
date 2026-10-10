# Scripted flows

Each `*.json` file here is a flow that `make verify` runs after visiting the pages, at phone, tablet and desktop widths (or the `viewports` it lists). A pull request adds steps by adding or editing a file.

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

- `page` is the route the flow starts on (default `overview`); `viewports` defaults to all three.
- `"signed_in": true` runs the flow signed in: against a second demo server with sign-in on (runway/verify.py), with a session made for it and a virtual platform authenticator (Chrome's DevTools WebAuthn) standing in for Face ID or Touch ID. The app lock's flow (`app-lock.json`) needs it. Each viewport is a new browser on the same session, so a flow that changes something about the session puts it back at the end.
- A step has one action, and an optional `timeout` in milliseconds (default 10000). Selectors are [Playwright selectors](https://playwright.dev/docs/other-locators) (CSS, `text=…`, `role=…`).
  - `{"goto": "#route"}` opens a route of the app (or a full URL).
  - `{"click": selector}`, `{"fill": {"selector": …, "text": …}}`, `{"press": {"selector": …, "key": "Enter"}}`.
  - `{"scroll_to": selector}` scrolls it to the middle of the window (and, inside a table that scrolls sideways, to its left edge), so the `-top` screenshot after it shows it.
  - `{"wait_for": selector}` waits for it to appear; `{"expect_text": {"selector": …, "text": …}}` fails unless it contains the text.
  - `{"reload": true}` opens the app again (a launch), not just another route.
  - `{"authenticator": "verified"}` or `"unverified"` (signed-in flows only): the virtual authenticator's Face ID works, or fails.
  - `{"screenshot": "name"}` saves `flow-<flow name>-<name>-<viewport>.png` (full page) and `…-<viewport>-top.png` (the top of the page, the size of the viewport).
- A flow also ends with a screenshot, and fails the run on a failed step, a console error or a 5xx response.

Flows run against the demo data only.
