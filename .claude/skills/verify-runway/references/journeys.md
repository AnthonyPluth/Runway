# Journeys

End-to-end recipes. Each assumes `tools/control doctor` passed. Run them from the repository root and look at every screenshot you take.

## 1. Reproduce a UI bug on Transactions

```sh
tools/control start --viewport desktop          # desktop: the row buttons show on hover there
tools/control goto transactions
tools/control snapshot --root '[data-tx-list]' --text --max-lines 40   # the names to target
tools/control click button 'Delete Corner Coffee' --nth 0
tools/control snapshot --root '[data-slot=confirm-dialog]' --text      # what the dialog says
tools/control screenshot                          # the window, since a dialog is open
tools/control console --text                      # any error is a bug
tools/control network --text                      # any 5xx is a bug
tools/control click button Cancel
```

Write down the steps that show the bug and the command output that proves it. Then turn it into a failing test, or into a flow in `frontend/verify/flows/`. `reset` gets you fresh data for another attempt.

## 2. Check a layout at both widths

```sh
tools/control start
tools/control goto budget
tools/control screenshot                           # phone, 440×956, the whole page
tools/control screenshot --viewport                # phone, the first screen as a person sees it
tools/control viewport desktop
tools/control screenshot                           # desktop, 1280×800
```

Open all three images. A phone layout that overflows, a clipped label or a floating bar over content shows up there. There is no tablet width: check 768 to 1023 px in a browser by hand.

## 3. Verify a Settings change

Here a credit card gets the account that pays it, and Overview's "choose which account pays it" item goes away.

```sh
tools/control start --viewport desktop
tools/control goto overview
tools/control snapshot --text | grep 'choose which account pays'    # it's there
tools/control goto '#setup'
tools/control click Travel Mastercard              # opens the row
tools/control type combobox 'Paid from' 'Everyday Checking'         # a select: picks the option
tools/control screenshot --viewport                # the row reads "paid from Everyday Checking"
tools/control goto overview --reload               # --reload: the app reloads what it has in memory
tools/control snapshot --text | grep 'choose which account pays'    # gone
tools/control reset                                 # back to the demo data
```

Without `--reload`, Overview still shows what it loaded before the change. The app's minute refresh is off here, as it is in make verify.

## 4. Compare before and after a change to the web app

```sh
tools/control start
tools/control goto budget
tools/control screenshot artifacts/control/before-budget-phone.png
# ...edit frontend/src...
(cd frontend && npm run build)
tools/control goto budget --reload                 # the server serves the new build at once
tools/control screenshot artifacts/control/after-budget-phone.png
cmp artifacts/control/before-budget-phone.png artifacts/control/after-budget-phone.png || echo changed
```

Look at both images. These are for you. The pull request's before and after come from `make screenshots PAGES="budget"`, whose diff shows in Files changed. Never commit anything from `artifacts/`.

## 5. Run one of make verify's flows step by step

```sh
tools/control flows --text                         # names, start pages, widths
tools/control flow budget-edit --text              # each step ok or FAIL, with the time it took
tools/control flow transactions-delete --viewport desktop
```

A failed step stops the flow, saves `artifacts/control/flows/flow-<name>-FAILED-<width>.png`, and exits 1. On data the flow didn't change, its images match `screenshots/` byte for byte. Run `reset` before a second run.

## 6. Drive a bank sync against the stand-in bank

A session's demo server has a stand-in SimpleFIN bridge beside it (`tests/fakes/simplefin_server.py`), with the demo's accounts and a few purchases the demo doesn't have yet. Sync now, a sync that fails and connecting with a setup token all run against it; nothing leaves the machine.

```sh
tools/control start --viewport desktop
tools/control bank --text                          # mode: ok
tools/control click button 'Sync now'              # the sidebar's button (on a phone: click More first)
tools/control goto transactions --reload
tools/control snapshot --root '[data-tx-list]' --text --max-lines 40   # Taco Place (pending), Corner Coffee, Green Grocer Market
tools/control bank 403                             # the bridge now refuses: access revoked
tools/control click button 'Sync now'
tools/control network --text | grep 502            # POST /api/sync 502: expected here, a bug anywhere else
tools/control goto '#setup/connections'
tools/control snapshot --text | grep -A3 'Needs attention'   # "The last sync didn’t finish…"; Details has "Reconnect in Settings."
tools/control screenshot
tools/control bank ok                              # put it back before anything else
```

To connect with a setup token, open SimpleFIN under Settings → Connections, choose "Replace the setup token" and paste this one (it is the same every time, and brings one more account, Vacation Savings):

```sh
tools/control goto '#setup/connections'
tools/control click 'details[data-group=banks] summary:has-text("SimpleFIN")'
tools/control click button 'Replace the setup token'
tools/control type textbox 'SimpleFIN setup token' 'aHR0cHM6Ly9zYW1wbGUtYmFuay5pbnZhbGlkL2NsYWltL2RlbW8tc2V0dXAtdG9rZW4='
tools/control click button 'Connect and sync'
tools/control goto '#setup/accounts' --reload
tools/control snapshot --text | grep 'Vacation Savings'
```

`bank 401` and `bank 500` fail the sync with the bridge's status in the message, `bank slow` makes each answer take two seconds (the Sync button spins), and `bank partial` leaves one institution out: the sync works and the sidebar says a bank needs attention. `reset` starts a new bank at `ok`. The flows `sync-now`, `sync-reconnect` and `simplefin-connect` (and their `-phone` versions) run the same steps in make verify. Plaid, Carta, Realie and Finnhub's live stream have no stand-in: test those with their unit tests.
