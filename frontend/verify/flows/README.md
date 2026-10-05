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
- A step has one action, and an optional `timeout` in milliseconds (default 10000). Selectors are [Playwright selectors](https://playwright.dev/docs/other-locators) (CSS, `text=…`, `role=…`).
  - `{"goto": "#route"}` opens a route of the app (or a full URL).
  - `{"click": selector}`, `{"fill": {"selector": …, "text": …}}`, `{"press": {"selector": …, "key": "Enter"}}`.
  - `{"wait_for": selector}` waits for it to appear; `{"expect_text": {"selector": …, "text": …}}` fails unless it contains the text.
  - `{"screenshot": "name"}` saves `flow-<flow name>-<name>-<viewport>.png`.
- A flow also ends with a screenshot, and fails the run on a failed step, a console error or a 5xx response.

Flows run against the demo data only.
