# Feature gotchas

Hand-edited. `tools/user_feature_map.py` merges these into [feature-map.md](feature-map.md) (generated; never edit that one). A `## <name>` heading must be a page (`overview`, `budget`, ...) or a flow's name (a file in `frontend/verify/flows/`), and only `- ` bullets go under it. Demo data only; no real figures or names. General advice is in [gotchas.md](gotchas.md).

## transactions
- A row's Delete button only shows on hover at desktop width; at phone width open the row's details instead.
- `snapshot --text` before clicking: the rows load after the page renders, so a click straight after `goto` can miss them.

## budget
- Amounts apply to every month unless "This month only" is chosen; check which scope a dialog is on before saving.

## setup
- Settings has tabs that are routes of their own (`#setup/categories`, `#setup/data`); `goto` the tab, not just `setup`.

## app-lock
- Needs `tools/control start --signed-in`; it turns the lock on and must turn it off again, or later steps land on the Unlock screen.

## transactions-delete
- The confirm dialog is the thing to look at; `Cancel` it, since the flow never confirms the delete.

## more-sheet-focus
- Runs at phone and landscape (844×390); the More sheet is the phone's way to reach pages that aren't in the tab bar.
