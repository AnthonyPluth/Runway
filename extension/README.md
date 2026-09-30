# Runway's browser extension: Amazon, Target, Costco and Carta

Amazon, Target and Costco don't offer an API for your own order history, so this small extension reads it the way their
websites do, with the sign-in already in your browser, and sends it to **your** Runway. Runway then matches each card
charge to its order and splits the transaction by what you bought ($84 at Target: $52 Groceries, $32 Household).

- **Amazon:** your Payments → Transactions pages (every card charge, with its order number; Amazon charges each
  shipment separately) and each order's details page.
- **Target:** online orders and in-store purchases linked to your Target account (Target Circle, the Wallet barcode,
  a saved card or your phone number at the register).
- **Costco:** your warehouse, gas station and car wash receipts, as costco.com's Orders & Purchases page lists them,
  each with all of its items (instant savings are taken off the items they're for). The extension opens your Costco
  account page, which sets up its sign-in in the browser's storage, and asks Costco's order service for your receipts
  from inside that page, 90 days at a time, the way the page does. The sign-in never leaves the page: only the
  receipts go to Runway. Orders placed on costco.com aren't read yet.

- **Carta:** your stock options, RSUs and shares, and each company's latest share price, as carta.com shows them to
  you. The extension loads Carta out of sight, notes the data requests Carta's own pages make, reads the same
  addresses again (reads only, carta.com only; never sign-out, exercise, accept or download links) and sends the
  replies to Runway, which finds your companies and grants in them. Click **Carta** in the extension; once an import
  has worked, the daily import includes it: every day, or about once a week or month if you
  choose that in Options (Carta signs you out often, so reading it less often means fewer sign-ins).

It sends pages only to the Runway address you set, and never sees your passwords. All the reading of those pages
happens in Runway (the extension just fetches them), so when a store changes its site the fix is a Runway update.

## Install

Chrome, Edge, Brave, Arc or any other Chromium browser:

1. In Runway, open **Settings → Browser extension** (the card that makes the key) and click **Make a key**. Copy it.
   A key works for 90 days (Settings shows when it runs out, and when the extension last used it), then you make a
   new one; it also stops working if the person who made it can no longer sign in to Runway.
2. Click **Download the extension** on that same card and unzip it. Go to `chrome://extensions`, turn on
   **Developer mode**, click **Load unpacked** and choose the unzipped `runway-orders` folder (or this `extension`
   folder, if you have the repository).
3. The options page opens: enter Runway's address (the one you open it at) and the key, then **Save and test**.
   Allow the extension to reach that address when asked.
4. Stay signed in to amazon.com, target.com and costco.com in that browser, and click the extension's toolbar button →
   **Import all** (Amazon and Target), or **Costco** for Costco. The first import reads six months back; later ones
   pick up where the last left off. Once Costco has worked, **Import all** and the daily import include it.

Tick **Import once a day** in the options to have it run by itself while the browser is open.

## Out of sight

Each store is read in a hidden frame of its own site (in Chrome, inside the extension's offscreen document; in
Firefox, inside the extension's background page), so no tab or window opens. For those frames only (requests from no
tab), the extension drops the stores' "don't show me in a frame" headers, and it puts its small reader (`frame.js`)
in them for the length of the import. It never runs in a store tab you have open yourself.

If a store won't load in a hidden frame, or looks signed out there, that import is done in a background tab instead,
as earlier versions did, and that store keeps to tabs for a week (or until the extension is updated). A tab comes to
the front only when you need to sign in.

It's written to load in Firefox 128 and later as well (`about:debugging` → **This Firefox** → **Load Temporary
Add-on…**, pick `manifest.json`; Firefox forgets temporary add-ons when it restarts), but it has only been tried in
Chromium so far.

## If Runway is behind a sign-in proxy

The extension signs its calls with the key rather than a sign-in, so a proxy in front of Runway (Authelia, Cloudflare
Access, …) must let `/api/ext/*` through to Runway. Runway checks the key on every one of those calls. What the key
can do is what the extension does: bring in orders and Carta grants, which Runway then matches to your card
transactions (setting their category and splits) and to your equity; it can't read your finances, settings or bank
connections.

## When a store changes its site

Runway reads the pages with the [amazon-orders](https://github.com/alexdlaird/amazon-orders) library's parsers for
Amazon, and loosely (by field names) for Target, whose order API is undocumented. Runway tells the extension which
address to read Target's history from (`TARGET_HISTORY` in `runway/server/api/retail.py`: the newer one, 100 orders
a page, then the older one if that isn't answered); Target says "too many requests" now and then, which the extension
waits out once (and goes slower after) before it gives up until the next import. Target's replies are kept with each
order, so if items are missing you can look at what Target sent (it's in the `retail_orders.raw` column) and Runway
can be taught to read it. When the addresses Runway knows for a Target order's items don't answer, the extension
loads that order's own page on target.com, reads the addresses the page called for it, and remembers them for the
next orders. An order whose page can't be read is tried again on the next few imports, then left alone.

For Costco, Runway holds the query and the list of headers to send (`COSTCO_GRAPHQL_CONFIG` in
`runway/server/api/retail.py`) and reads the replies loosely (by field names); each receipt is kept with its order
(`retail_orders.raw`). If the extension says it couldn't find how costco.com signs its requests, open
Account → Orders & Purchases in that browser once and import again; if it still can't, the address of the account
page (`page` in that config) or the names of the values the page keeps in storage (`storage_headers`) need updating,
which is a change in Runway only.

For Carta, whose web app isn't documented either, Runway keeps what the extension read: Settings -> Browser extension ->
Carta -> **Download what the extension read** shows why a grant was missed.
