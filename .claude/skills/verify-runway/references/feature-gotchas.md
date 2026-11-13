# Feature gotchas

Hand-edited. `tools/user_feature_map.py` merges these into [feature-map.md](feature-map.md) (generated; never edit that one). A `## <name>` heading must be a page (`overview`, `budget`, ...) or a flow's name (a file in `frontend/verify/flows/`), and only `- ` bullets go under it. Demo data only; no real figures or names. General advice is in [gotchas.md](gotchas.md).

## transactions
- A row's Delete button only shows on hover at desktop width; at phone width open the row's details instead.
- `snapshot --text` before clicking: the rows load after the page renders, so a click straight after `goto` can miss them.

## budget
- Amounts apply to every month unless "This month only" is chosen; check which scope a dialog is on before saving.

## setup
- Settings has tabs that are routes of their own (`#setup/categories`, `#setup/data`); `goto` the tab, not just `setup`.
- Connections: only SimpleFIN and the price service have a stand-in beside the demo server (`tools/control bank`, journeys.md's recipe 6). Plaid (its Link widget loads from Plaid), Carta, Realie, Finnhub's live stream, Logo.dev and the AI provider have none: their requests go nowhere, so check those with their unit tests.

## app-lock
- Needs `tools/control start --signed-in`; it turns the lock on and must turn it off again, or later steps land on the Unlock screen.

## transactions-delete
- The confirm dialog is the thing to look at; `Cancel` it, since the flow never confirms the delete.

## more-sheet-focus
- Runs at phone and landscape (844×390); the More sheet is the phone's way to reach pages that aren't in the tab bar.

## simplefin-connect
- The setup token is the same every time (it's in the flow's file); claiming it gives a connection with one more account, Vacation Savings. The demo is already connected, so the flow goes through "Replace the setup token".
- After connecting, the page reloads and the SimpleFIN row closes: open it again before reading it.

## simplefin-connect-phone
- The same steps as simplefin-connect, at phone width, on data of its own.

## sync-now
- The first sync brings three card purchases (one pending) and splits the demo's Target purchase into its receipt's parts; a second sync brings only the pending one again.
- The Sync button is in the sidebar at desktop width; on a phone it is in the More sheet (sync-now-phone).

## sync-now-phone
- Open More first: the Sync button and the sync status are in that sheet. Close it before looking at Transactions.

## sync-reconnect
- `tools/control bank 403` (a flow's `{"bank": "403"}`) makes the stand-in bank refuse; the app's 502 from `/api/sync` is expected then, and the reconnect message is under Details in Settings → Connections → SimpleFIN.
- Only a 403 gets "Reconnect in Settings": a 401 or a 500 fails the sync with the bridge's status in the message instead.
- Set the bank back (`bank ok`) afterwards, or every later sync in the session fails too.

## sync-reconnect-phone
- The same as sync-reconnect, with the Sync button reached through More.
