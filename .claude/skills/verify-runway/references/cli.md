# tools/control: command reference

Generated from the CLI's own help by `make verify-skill` (`node frontend/verify/control.mjs help --markdown`); `make fleet-checks` fails when it is out of date. Don't edit it by hand.

```text
tools/control: drive the real app on demo data, one command at a time, and prove what you changed.

Usage: tools/control <command> [arguments] [options]

Start with `doctor`, then `start`; finish with `stop`. Output is JSON ({"ok": true, …}, or {"ok": false, "error": {code, message, hint}});
--text prints it for people. Exit 0 on success, 1 when the command failed, 2 for a usage error. `<command> --help` says more.

Session:
  doctor       Check this machine can run a session, and say how to fix what can't.
  start        Start a session: a demo server on fresh demo data under the frozen clock, and a browser on it.
  stop         Stop the session: its browser, its demo server and its copy of the data.
  cleanup      Remove what a crashed session left: its state file, data folders and processes.
  reset        Fresh demo data: a new copy of the seed on a new demo server, and the page opened again.

Navigate:
  goto         Open a route of the app.
  viewport     Resize the browser to the phone (440×956) or desktop (1280×800) size make verify uses.

Inspect:
  info         Where the browser is: route, title, viewport, the frozen clock and the session.
  snapshot     The page's accessibility tree as text: roles and the names to click.
  screenshot   Save a screenshot once the page has settled, and print its path.
  wait-settle  Wait until the page has settled, by make verify's rule, and say what is still loading if not.

Interact:
  click        Click a target (a role and name, a selector or text).
  type         Fill a field with text, or pick a select's option (the last argument is the text).
  press        Press a key, or a chord, in the focused element.
  scroll       Scroll the page: up, down, top, bottom, or until a target is in view.
  eval         Evaluate JavaScript in the page and print the result (read-only unless --write).

Observe:
  console      The console messages and page errors since the last call.
  network      The requests since the last call: method, path, status, and the ones that failed.

Flows:
  flows        List make verify's flows (frontend/verify/flows/), with the page and widths each starts on.
  flow         Run one verify flow on the session, step by step, and report each step.

Help:
  help         This overview, or one command's help (`help <command>`, the same as `<command> --help`).

Localhost and demo data only: it drives only the demo server it started, and writes only under artifacts/control/ (never committed).
```

## Session

### doctor

```text
tools/control doctor

Check this machine can run a session, and say how to fix what can't.

Checks Node, the web app's packages and build, Chromium, Python with Runway's packages, libfaketime (the frozen clock), a free loopback port,
the machine's load, a running make verify, this checkout's session (none, running or stale) and processes a crashed session left.
Each check has ok, detail and, when it fails, fix: the command to run. Exits 1 when a check fails.

Examples:
  tools/control doctor --text
```

### start

```text
tools/control start [options]

Start a session: a demo server on fresh demo data under the frozen clock, and a browser on it.

Without a seed option it seeds what make verify does (ai-buttons, investments, receipt, subcategories), so pages look as in screenshots/.
The server and the seed run under libfaketime at make verify's frozen day, and the browser's clock is fixed at noon that day.
One session per checkout, on ports and data of its own: it never takes make verify's machine lock, and a running make verify is left alone.
Refuses when the machine is busy (make verify's load check) unless --anyway. A stale session (its process gone) is cleaned up first.
Isolated: the browser reaches only its own demo server (a request anywhere else, another port on this machine included, is blocked before it's sent), and the session's processes get only what runway/verify.py's KEPT_ENV lists of your environment, so no API key or token in your shell reaches them.

Options:
  --ai-buttons  seed what the AI buttons need
  --investments  seed a sample brokerage
  --receipt  seed a store purchase with its receipt
  --subcategories  seed subcategories with transactions in them
  --plain  only the plain demo data (none of the above)
  --signed-in  sign-in on, the browser signed in (for the app lock and signed_in flows)
  --viewport <phone|desktop>  the browser's size to start at (default phone)
  --idle-minutes <number>  stop by itself after this long with no command (default 30)
  --anyway  start even though the machine is busy (waits stretch with the load)

Examples:
  tools/control start
  tools/control start --viewport desktop
  tools/control start --plain --signed-in
```

### stop

```text
tools/control stop [options]

Stop the session: its browser, its demo server and its copy of the data.

Screenshots under artifacts/control/ stay (cleanup --shots removes them). Stopping when there is no session is not an error.

Options:
  --dry-run  say what it would do, and do nothing

Examples:
  tools/control stop
  tools/control stop --dry-run
```

### cleanup

```text
tools/control cleanup [options]

Remove what a crashed session left: its state file, data folders and processes.

Only ever this checkout's control files and processes (found by artifacts/control/ in their command line or data folder).
A running session is left alone: stop it first.

Options:
  --dry-run  say what it would do, and do nothing
  --shots  also delete the screenshots under artifacts/control/

Examples:
  tools/control cleanup --dry-run
  tools/control cleanup --shots
```

### reset

```text
tools/control reset [options]

Fresh demo data: a new copy of the seed on a new demo server, and the page opened again.

What you changed (deleted a transaction, edited a budget) is gone. The browser, viewport and route stay; the port changes.

Options:
  --dry-run  say what it would do, and do nothing

Examples:
  tools/control reset --dry-run
  tools/control reset
```

## Navigate

### goto

```text
tools/control goto <route> [options]

Open a route of the app.

A route is `budget`, `#setup/categories` or `/#transactions`; a full URL only on the session's own server (anything else is refused).
Waits for the page to settle (as wait-settle) and says whether it did.
The app's once-a-minute refresh is off here, as in make verify: after changing data, a page can show what the app had loaded before. --reload shows the server's.

Options:
  --reload  then open the app again (a launch): it reloads what it keeps in memory

Examples:
  tools/control goto transactions
  tools/control goto '#setup/categories'
  tools/control goto overview --reload
```

### viewport

```text
tools/control viewport <phone|desktop>

Resize the browser to the phone (440×956) or desktop (1280×800) size make verify uses.

There is no tablet size (make verify has none): look at 768–1023 px by hand.

Examples:
  tools/control viewport desktop
```

## Inspect

### info

```text
tools/control info

Where the browser is: route, title, viewport, the frozen clock and the session.

Examples:
  tools/control info --text
```

### snapshot

```text
tools/control snapshot [options]

The page's accessibility tree as text: roles and the names to click.

Each line is `- role "name"`, indented by nesting: the role and name are what click, type and the role target take.

Options:
  --root <string>  only this part of the page (a selector), e.g. [role=dialog]
  --max-lines <number>  cut the tree after this many lines (it says so) (default 300)

Examples:
  tools/control snapshot --text
  tools/control snapshot --root '[role=dialog]' --text
```

### screenshot

```text
tools/control screenshot [path] [options]

Save a screenshot once the page has settled, and print its path.

Taken as make verify takes one: settled, toasts hidden, endless animations stopped, the pointer moved away, two captures that match.
A full-page shot scrolls to the top first; with a dialog or sheet open it's the window, as make verify does.
The path must be under artifacts/ (default artifacts/control/shots/<n>-<route>-<viewport>.png). Look at it: it is the evidence.
Never copy one into screenshots/: that directory is only what make screenshots writes.

Options:
  --full  the whole page (the default)
  --viewport  only the window, where it's scrolled to

Examples:
  tools/control screenshot
  tools/control screenshot --viewport
  tools/control screenshot artifacts/control/before.png
```

### wait-settle

```text
tools/control wait-settle [options]

Wait until the page has settled, by make verify's rule, and say what is still loading if not.

Settled: no skeleton placeholder, nothing aria-busy, no transition still running, the Investments live prices in, fonts and document loaded.

Options:
  --timeout <number>  how long to wait, in ms (stretched on a busy machine) (default 20000)

Examples:
  tools/control wait-settle
```

## Interact

### click

```text
tools/control click <target…> [options]

Click a target (a role and name, a selector or text).

A target is one of: a role and its accessible name (`button Save`, `link Budget`, `textbox Search`: the names `snapshot` shows),
a selector (`#id`, `.class`, `[data-testid=x]`, `css=…`, `text=…`, `role=…`, `button:has-text('Save')`), or else visible text (`Add transaction`).
Several matches are an error that lists them: pick one with --nth, or name it more exactly.

Options:
  --nth <number>  which of several matches to use (0 is the first); without it, more than one match is an error
  --exact  match the name or text exactly (case-sensitive), not as a substring
  --timeout <number>  how long to wait for the target, in ms (stretched on a busy machine) (default 10000)

Examples:
  tools/control click button Save
  tools/control click link Budget --nth 1
  tools/control click '[data-tx] button[aria-label^="Delete "]'
```

### type

```text
tools/control type <target…> <text> [options]

Fill a field with text, or pick a select's option (the last argument is the text).

A target is one of: a role and its accessible name (`button Save`, `link Budget`, `textbox Search`: the names `snapshot` shows),
a selector (`#id`, `.class`, `[data-testid=x]`, `css=…`, `text=…`, `role=…`, `button:has-text('Save')`), or else visible text (`Add transaction`).
Several matches are an error that lists them: pick one with --nth, or name it more exactly.
The field's current value is replaced. On a select (a combobox with options) it picks the option with that label.
Text that starts with - goes after --: `type textbox Amount -- -5`.

Options:
  --nth <number>  which of several matches to use (0 is the first); without it, more than one match is an error
  --exact  match the name or text exactly (case-sensitive), not as a substring
  --timeout <number>  how long to wait for the target, in ms (stretched on a busy machine) (default 10000)

Examples:
  tools/control type textbox Search coffee
  tools/control type '#amount' 12.50
  tools/control type combobox 'Paid from' 'Everyday Checking'
```

### press

```text
tools/control press <key>

Press a key, or a chord, in the focused element.

Keys are Playwright's names: Enter, Escape, Tab, Shift+Tab, ArrowDown, Control+a.

Examples:
  tools/control press Escape
  tools/control press Shift+Tab
```

### scroll

```text
tools/control scroll [where…] [options]

Scroll the page: up, down, top, bottom, or until a target is in view.

Without an argument it scrolls down a screen.

Options:
  --nth <number>  which of several matches to use (0 is the first); without it, more than one match is an error
  --exact  match the name or text exactly (case-sensitive), not as a substring

Examples:
  tools/control scroll
  tools/control scroll bottom
  tools/control scroll heading Holdings
```

### eval

```text
tools/control eval <js…> [options]

Evaluate JavaScript in the page and print the result (read-only unless --write).

An expression (`document.title`) or a function (`() => [...document.querySelectorAll('h2')].map(h => h.textContent)`); the result as JSON.
Read-only by default: a request it makes to the app's data (other than GET or HEAD) is blocked before it's sent, and the eval fails with not_read_only. It waits half a second, and for the network to go quiet, before it answers, so a change set off on a short timer fails it too; one set off later is blocked all the same, and reported as a warning by the next command. A change it makes to the page is reported (domChanges).
With --write it may change the demo data. The guard is off only while a command that acts runs (goto, viewport, click, type, press, scroll, flow, reset, eval --write) and for a second after it (a field that saves itself once you stop typing): a timer an expression leaves running that fires then is not blocked. Reload the page (goto --reload) to clear what an expression left behind.
Like every command it reaches only the session's own server: a request anywhere else is blocked before it's sent.
Prefer snapshot, click and type: they act as a person would.

Options:
  --write  allow it to change the app's data (requests other than GET)

Examples:
  tools/control eval document.title
  tools/control eval "() => getComputedStyle(document.body).backgroundColor"
```

## Observe

### console

```text
tools/control console

The console messages and page errors since the last call.

make verify fails a page on a console error or an uncaught page error: so should you.

Examples:
  tools/control console --text
```

### network

```text
tools/control network

The requests since the last call: method, path, status, and the ones that failed.

Paths only: no query strings, bodies or ids (numbers become :id). make verify fails a page on a 5xx.

Examples:
  tools/control network --text
```

## Flows

### flows

```text
tools/control flows

List make verify's flows (frontend/verify/flows/), with the page and widths each starts on.

Examples:
  tools/control flows --text
```

### flow

```text
tools/control flow <name> [options]

Run one verify flow on the session, step by step, and report each step.

Its screenshots go to artifacts/control/flows/, never artifacts/verify/ or screenshots/. It stops at the first failed step, with a -FAILED- image.
A flow starts from the demo data as make verify seeds it: run `reset` first if you changed anything. A signed_in flow needs `start --signed-in`.

Options:
  --viewport <phone|desktop>  resize to this first (default: the session's)

Examples:
  tools/control flow budget-edit
  tools/control flow transactions-delete --viewport desktop
```

## Help

### help

```text
tools/control help [command] [options]

This overview, or one command's help (`help <command>`, the same as `<command> --help`).

Options:
  --markdown  the whole reference as Markdown (references/cli.md)

Examples:
  tools/control help click
```
