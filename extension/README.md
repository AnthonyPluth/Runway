# Runway orders (browser extension)

Amazon and Target don't offer an API for your own order history, so this small extension reads it the way their
websites do, with the sign-in already in your browser, and sends it to **your** Runway. Runway then matches each card
charge to its order and splits the transaction by what you bought ($84 at Target: $52 Groceries, $32 Household).

- **Amazon:** your Payments → Transactions pages (every card charge, with its order number; Amazon charges each
  shipment separately) and each order's details page.
- **Target:** online orders and in-store purchases linked to your Target account (Target Circle, the Wallet barcode,
  a saved card or your phone number at the register).

It sends pages only to the Runway address you set, and never sees your passwords. All the reading of those pages
happens in Runway (the extension just fetches them), so when a store changes its site the fix is a Runway update.

## Install

Chrome, Edge, Brave, Arc or any other Chromium browser:

1. In Runway, open **Settings → Connections → Amazon and Target** and click **Make a key**. Copy it.
2. Click **Download the extension** on that same card and unzip it. Go to `chrome://extensions`, turn on
   **Developer mode**, click **Load unpacked** and choose the unzipped `runway-orders` folder (or this `extension`
   folder, if you have the repository).
3. The options page opens: enter Runway's address (the one you open it at) and the key, then **Save and test**.
   Allow the extension to reach that address when asked.
4. Stay signed in to amazon.com and target.com in that browser, and click the extension's toolbar button →
   **Import both**. The first import reads six months back; later ones pick up where the last left off.

Tick **Import once a day** in the options to have it run by itself while the browser is open (it opens the stores in
background tabs and closes them when done).

It's written to load in Firefox 128 and later as well (`about:debugging` → **This Firefox** → **Load Temporary
Add-on…**, pick `manifest.json`; Firefox forgets temporary add-ons when it restarts), but it has only been tried in
Chromium so far.

## If Runway is behind a sign-in proxy

The extension signs its calls with the key rather than a sign-in, so a proxy in front of Runway (Authelia, Cloudflare
Access, …) must let `/api/ext/*` through to Runway. Runway checks the key on every one of those calls, and they can
only add orders.

## When a store changes its site

Runway reads the pages with the [amazon-orders](https://github.com/alexdlaird/amazon-orders) library's parsers for
Amazon, and loosely (by field names) for Target, whose order API is undocumented. Target's replies are kept with each
order, so if items are missing you can look at what Target sent (it's in the `retail_orders.raw` column) and Runway
can be taught to read it. An order whose page can't be read is tried again on the next few imports, then left alone.
