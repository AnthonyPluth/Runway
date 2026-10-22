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
