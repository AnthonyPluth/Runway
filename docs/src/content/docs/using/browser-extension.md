---
title: Browser extension
description: Bring in Amazon, Target, Costco and Carta orders and grants.
sidebar:
  order: 10
---

Amazon, Target and Costco don't offer an API for your own order history, so this small extension reads it the way their
websites do, with the sign-in already in your browser, and sends it to **your** Runway. Runway then matches each card
charge to its order and splits the transaction by what you bought ($84 at Target: $52 Groceries, $32 Household).
A matched transaction has a **receipt** badge on Transactions: click it to see what was in the order. A category picked for an item there applies to that item in every order. **Split by items**, **Not this transaction** and an item's category can each be undone from their message.

- **Amazon:** your Payments → Transactions pages (every card charge, with its order number; Amazon charges each
  shipment separately) and each order's details page.
- **Target:** online orders and in-store purchases linked to your Target account (Target Circle, the Wallet barcode,
  a saved card or your phone number at the register).
- **Costco:** your warehouse, gas station and car wash receipts, as costco.com's Orders & Purchases page lists them,
  each with all of its items (instant savings are taken off the items they're for). Your Costco sign-in never leaves
  the browser: only the receipts go to Runway. Orders placed on costco.com aren't read yet.
- **Carta:** your stock options, RSUs and shares, and each company's latest share price, as carta.com shows them to
  you. It reads only; it never follows sign-out, exercise, accept or download links. Click **Carta** in the
  extension; once an import has worked, the daily import includes it, or about once a week or month if you choose
  that in Options (Carta signs you out often, so reading it less often means fewer sign-ins).

It sends pages only to the Runway address you set, and never sees your passwords.

## Install

Chrome, Edge, Brave, Arc or any other Chromium browser:

1. In Runway, open **Settings → Connections** (the card that makes the key) and click **Make a key**. Copy it.
   A key doesn't expire: it works until you make a new one (which replaces it) or remove it on that card, or until
   the person who made it can no longer sign in to Runway. Settings shows when the extension last used it. Treat it
   like a password: if it may have got out, make a new one. Keys used to last 90 days; one that had already passed
   its 90 days when you updated Runway was removed then, so make a new one if the extension says Runway doesn't know
   its key.
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

Each store is read in a hidden frame, so no tab or window opens, and the extension never runs in a store tab you have
open yourself. If a store won't load in a hidden frame, or looks signed out there, that import runs in a background
tab instead. A tab comes to the front only when you need to sign in.

## When a store wants you to sign in

If a store has signed you out (or wants a robot check answered), the import stops for that store and opens its
sign-in page in a tab, brought to the front. Sign in there and the import carries on by itself. The extension waits up
to 12 hours, or until you close that tab.

The daily import may run while you're busy with something else, so it doesn't take the focus: the window asks for
your attention in the taskbar or Dock, and Runway sends a notification to the devices of the person who made the
extension's key, if they've turned on **Browser extension needs a sign-in** under Settings → Notifications. It names
only the store, and is sent once per store while the sign-in waits.

The extension also loads in Firefox 128 and later (`about:debugging` → **This Firefox** → **Load Temporary
Add-on…**, pick `manifest.json`; Firefox forgets temporary add-ons when it restarts), but has only been tried in
Chromium so far.

## If Runway is behind a sign-in proxy

The extension signs its calls with the key rather than a sign-in, so a proxy in front of Runway (Authelia, Cloudflare
Access, …) must let `/api/ext/*` through to Runway. Runway checks the key on every one of those calls. What the key
can do is what the extension does: bring in orders and Carta grants, which Runway then matches to your card
transactions (setting their category and splits) and to your equity; it can't read your finances, settings or bank
connections.

## When a store changes its site

The stores' order pages aren't documented, so an import can stop working when a store changes its site. The extension
only fetches pages; Runway reads them, so the fix is usually a Runway update, not a new extension.

- **Amazon** now gives its payments history after the first page as data rather than pages; extension 1.4.1 asks for
  it that way. An older extension reads only the first page again and again: download the extension again from
  Settings and reload it. If Amazon answers with a page it already gave, the import stops there, reads that page
  once, and tries again from the same date next time.
- **Target** says "too many requests" now and then. The extension waits it out and slows down, and gives up until the
  next import if it keeps happening. An order whose page can't be read is tried again on the next few imports, then
  left alone.
- **Costco:** if the extension says it couldn't find how costco.com signs its requests, open Account → Orders &
  Purchases in that browser once and import again.
- **Carta:** **Settings → Connections → Browser extension → Carta → Download what the extension read** shows what
  the extension sent, and why a grant was missed.
