---
title: Features
description: What each part of Runway does and where its settings live.
sidebar:
  order: 1
---

The [home page](/Runway/) has the short list. This is what each part does and where its settings live.

Runway's pages, in the sidebar (or the tab bar on a phone): **Overview**, **Transactions** (All and To review), **Budget** (Budget and Bills & income), **Reports**, then **Net worth** (Summary, Investments, Equity and Retirement) and **Churning**, and **Settings** at the bottom.

Settings has seven tabs: **Accounts**, **Connections** (SimpleFIN, Plaid and the browser extension for Amazon, Target, Costco and Carta), **Categories**, **Rules**, **Services** (OpenRouter, Realie, Finnhub and Logo.dev), **Notifications**, and **Advanced** (MCP keys, backup and restore, and the version). Until a bank is connected, Settings opens on Connections.

## Cash-flow forecast

On the Overview page.

- **Day-by-day projection** of your primary account for 30 days to 6 months, with the lowest point called out in plain language ("Checking stays above $2,084 for the next 120 days"), and a line under it saying what the forecast counts ("Includes 7 paychecks and bills and 2 card payments (1 estimated). Everyday spending isn’t included").
- **Forecast settings:** click the account name above the balance to choose the account, turn on **everyday spending** (the account’s recent card swipes, cash and other non-recurring spending, spread evenly over each day; it shows what that comes to a day) and set the default length. The account can also be chosen with “Use for the forecast” in Settings → Accounts, and everyday spending turned on there per account.
- **Credit cards paid the way you pay them:** each card's statement balance comes out of checking on its due date. Statement balances, closing dates, due dates and minimum payments come straight from the card issuer through Plaid (Liabilities), so there's nothing to set up by hand. You can still correct a statement figure.
- **Starting balance:** the bank's balance plus whatever is pending on the account, money in and out, so a pending debit isn't left out ("Balance as of today, including −$375.00 pending"). From SimpleFIN, only the last 14 days' pending transactions count, the ones a sync re-reads; Plaid removes the ones that are no longer pending, so all of its count. Some banks' balance already includes pending: banks take pending money out of the available balance, so when it's at or below the balance but hasn't come down by the pending money out, the balance has it already and it isn't added again. Pending money in, like a paycheck, is always added, and so is everything pending when there's no available balance or it's above the balance (an overdraft line counted in it). A pending paycheck already matched to its recurring item counts as paid, so it isn't added again.
- **Future statements** are estimated from each card's average spending over its last three statements. For the statement in progress, it's what's been charged so far plus the average's share of the days left before it closes, so a day before the close the estimate is little more than what's on the card. Paying more than a statement (the current balance, say) takes the extra off the next one. Recurring items on the card that the average can't stand for are left out of it and added on their dates instead, so each counts once: ones that come less often than monthly (a yearly insurance premium) and ones that started after the first averaged statement opened (a new subscription).
- **Everyday spending leaves out** payments to recurring items (a payment with an item's merchant text and an amount close to the item's, the same test matching uses, so a Prime item doesn't hide every Amazon order) and single payments over $1,000. When the last 90 days have payments over $1,000 that aren't transfers or recurring items, a warning names them so you can add them as recurring items (rent paid by hand, tuition); mark a true one-off "not recurring" and it stops being counted.
- **If you stick to your budget:** a dashed line spends each budget on its account or card, in place of the estimated card statements. This month's includes what a budget that rolls over carried into it. A budget on a card that isn't paid from the forecast account is left out and says so.
- **Business days:** a payment due on a weekend or bank holiday lands on the next business day, and a paycheck on the business day before (Federal Reserve holiday calendar).
- **One-off edits:** click any upcoming amount (dotted underline) to change it for that date only; a recurring item’s ↻ opens Bills & income to change every one.
- **Warnings link to their fix:** a card that isn’t linked, has no paying account or is missing a statement opens the Settings tab where that’s put right, and big payments the forecast leaves out open Bills & income.
- **Known limitations:** with more than one account in the forecast, a recurring transfer from one to another (to savings, say) only counts as money out unless you add the matching money in on the other account as its own recurring item. Plaid reports the date a statement was issued, which Runway takes as its closing date; for an issuer that posts the statement a day or two after it closes, charges in those days are counted toward the next statement rather than this one.
- **This month:** spending so far against the same point last month, the budgets closest to their limit, and the latest transactions.

## Connections

Under Settings → Connections, with each account's source chosen under Settings → Accounts.

- **SimpleFIN or Plaid for each account.** Link a bank or card through Plaid, match its accounts to the ones you already have, and pick where each one's balance and transactions come from. Switching keeps your history, categories and recurring matches: transactions both providers have are matched up so nothing is counted twice.
- Accounts only Plaid can reach can be added on their own, and a card linked through Plaid gets its statements from the issuer whichever provider its transactions come from. A mortgage or student loan linked through Plaid gets its interest rate, monthly payment and payoff date from the lender the same way (Plaid Liabilities), for the retirement planner; other loans take them from you in Settings → Accounts.
- **Automatic sync** once a day at 7 AM (Runway's time zone, America/Chicago by default), SimpleFIN and Plaid together, late enough for overnight ACH. Opening Runway only catches up a day's sync that was missed. At 6:30 AM Runway asks Plaid to fetch from the banks (Plaid's Transactions Refresh add-on), so the 7 AM sync is current. The Sync button syncs SimpleFIN whenever you press it; Plaid, whose quota is small, is asked once a day only, except by a connection's own Sync button.

## Bills & income

On Budget → Bills & income.

- Paychecks, mortgage, bills and subscriptions: weekly, every two weeks, twice a month, monthly, quarterly, twice a year, yearly, or on specific dates (property tax on April 15 and October 15, say).
- Transactions are **matched automatically** to their recurring item, and Runway **flags payments that didn't happen**.
- Adding one asks for a name, money out or money in (a bill or a paycheck), a positive amount, how often and the next date (today unless you change it); the account, the amount to forecast (the fixed amount, the last payment or the average of the last three) and the merchant text to match are under More options.
- Suggestions for recurring items it spots in your history. Add fills the form so you can adjust it first; Not recurring hides a suggestion for good.

## Transactions and categorization

- **Rules** (Settings → Rules) with conditions and actions: when the merchant contains, is or starts with some text, the amount is in a range, the money goes out or comes in, or it's in a particular account, then set a category, rename the merchant, split it by percentages ("Costco: 70% Groceries, 30% Household") or put it in Review. The most specific rule wins, and a preview shows what a rule would match before you save it. When you pick a category, Runway asks whether to use it for that merchant from now on, including after you apply an AI suggestion, so nothing writes a rule behind your back. Built-in heuristics handle card payments and sweeps.
- **Optional AI categorization** through OpenRouter (key under Settings → Connections), guided by examples of how you've categorized before and shown with confidence scores. Confident answers can be applied during sync; the rest wait for you, and it can propose new categories when nothing fits. Every AI call is logged.
- **Split transactions:** a $100 run to Target can be $60 Groceries and $40 Shopping. Budgets, reports and category filters count each part on its own; pick a single category again and the transaction goes back together.
- **Amazon, Target and Costco orders:** a small browser extension (installed from Settings → Connections) reads your orders with the sign-in already in your browser, and Runway splits each card charge by what you bought: tax and shipping shared out, each Amazon shipment matched to its own items. Items are categorized by the AI if you've set it up, and a category you pick for an item sticks for next time. See [Browser extension](/Runway/using/browser-extension/).
- **Bulk editing:** tick transactions (shift-click for a range) to give them a category, rename their merchant or mark them reviewed at once.
- **Categories** (Settings → Categories) have one level of subcategories, each with an emoji and a color. Anything uncategorized waits on the To review tab; there is also search and filtering. The list is grouped by day with each day's total, and loads more as you scroll.
- **Merchant logos** for merchants Plaid knows, downloaded once from Plaid and served by Runway itself. With a free Logo.dev publishable key (Settings → Connections), merchants Plaid has no logo for get one from Logo.dev by their website (Plaid's, or for about 95 big names like Target, Amazon and Walmart, one Runway knows): downloaded during a sync, re-checked monthly, served by Runway. The same key gives each bank or card its logo: by website for big banks Runway recognizes (Chase, Citi, Capital One, American Express and so on, also from a card's name), else by the institution's name; without it, accounts show a letter. To use a different logo for an account, or none, open the account in Settings → Accounts and click its logo.

## Budget and reports

- Monthly budgets per category with pace markers; click any amount to jump to the transactions behind it.
- **Rollover**, budget by budget: what's left at the end of a month is added to the next (going over isn't carried).
- **Reports** has five views: **Cash flow** (a Sankey chart of where each month's money went), **Over time** (spending by category, merchant or account over 6, 12 or 24 months, with each one's change from last month and a year ago), **Merchants** (ranked by what you spent, each with its months and transactions), **Income vs spending** (with your savings rate) and **Breakdown** (a treemap you click into, from categories to subcategories to merchants to the transactions behind them).

## Investments

On Net worth → Investments.

- Holdings and performance for brokerage and retirement accounts, modeled on Ghostfolio: time-weighted return, a comparison with the S&P 500, gain per holding and allocation.
- **Near-real-time prices** while the market is open, with each holding's gain today. With a free [Finnhub](https://finnhub.io) key (Settings → Connections) they become real trades, pushed the moment they happen for up to 50 of your stocks and ETFs. Runway holds one connection to Finnhub for the whole app and your browser never sees the key. Yahoo takes over for anything else, or if Finnhub can't be reached.
- Positions from SimpleFIN, or optionally from Plaid for accounts SimpleFIN only knows the balance of. Accounts that neither can see into, like some 401(k)s, can be **tracked by hand**: enter shares and your contribution split, and Runway invests each new deposit accordingly.
- Editable cost basis, per share.

## Retirement

On Net worth → Retirement.

- A retirement planner that projects your investments year by year to the age you plan for, across 1,000 simulated markets, with the chance your money lasts. Its assumptions (spending, saving, return, Social Security, pensions, one-off events, selling a home) start from your own numbers and keep whatever you change; **Start over** (after a confirmation) goes back to Runway's figures. Until you enter your own dates the results are marked as a sample, and with nothing invested yet the tab points you to Investments instead. It plans from your investments only, in today's dollars and before tax.
- **Selling a home or equity into the plan:** tick a home, vehicle or company's equity and choose the year to sell it. A home's value grows by its yearly change from the Net worth page, and the loan against it is paid down month by month to what will still be owed that year, using the loan's interest rate and monthly payment: the lender's, through Plaid, for mortgages and student loans, or what you enter on the loan in Settings → Accounts (an auto loan, or a loan from SimpleFIN). Left empty, the payment is the typical month's payments into the loan account lately. Without an interest rate the plan counts today's balance and says so. Equity counts what will have vested by then, at today's share price, including grants that haven't vested at all yet. Hover over the estimate to see what it assumes.

## Net worth and equity

- **Summary:** every account plus homes, vehicles and anything else you own, minus cards and loans, recorded daily. Optional automated home values through Realie (key under Settings → Connections; each home is looked up once a week at most, within the free tier).
- **Equity:** stock options (ISO/NSO), RSUs, restricted stock and shares, each with its vesting schedule (cliff, monthly or quarterly), exercise price and exercises. Runway shows what has vested and what's still to come at each company's latest share price, and counts the vested part in net worth. Enter grants by hand, or read them from **Carta** with the browser extension (with your Carta sign-in, like Amazon and Target) or with Carta's Portfolio API if Carta approves your app.

## Churning

Credit cards and bank accounts opened for their sign-up bonuses, for you and your partner, on three tabs: Cards, Benefits and Bank bonuses.

- **Cards found on your accounts:** every credit card account Runway already has that isn't a churning card yet appears under "Found on your accounts" (on larger screens), pre-filled from what the bank reported: whose it is, the bank (matched to the ones Churning knows, else Other), the card's name cleaned of the last digits and network words, the latest annual fee and the month it posted (from transactions named like "Annual fee"), a business card when its name says so, and the account's spending linked. **Add** opens the add-card form to review (nothing is saved until you save it); **Dismiss** hides one, and you can bring it back from the "dismissed" list. Banks don't say when a card was opened, so the date is the account's first transaction in Runway, marked "on or before": change it to the real day, since 5/24 and the bonus rules depend on it.
- **Fill in the rest with AI:** with an OpenRouter key set (Settings → Connections), the add and edit card forms offer a button that asks the model what it knows of the card: its family, what it earns, earning rates by your categories, annual fee, travel portal and benefits. Only the bank's name and the card's name are sent: no account, owner, balance or transaction. Anything that isn't one of your currencies or categories is dropped, and the rest fills only what's still empty, marked "Suggested by AI, check before saving". Models can be wrong or out of date; nothing is saved until you add the card (or choose Save these on an existing one).
- **5/24** per person (authorized-user, business and product-changed cards left out) with the day each card stops counting.
- **Annual fees** coming up (keep, downgrade or close?), or your plan for each card: keep it and it stops asking; downgrade or close it and you're reminded before the fee, with a Done that updates the card.
- **Bonus spending and direct deposits** followed from linked accounts, and when a bonus can be earned again by each bank's commonly reported rule (a rule of thumb, overridable).
- **Card benefits and credits**, what's left this period, and the annual fee net of the ones you use.
- **Bank accounts:** safe-to-close days and fee-waiver reminders, and bank bonus money per year (usually reported as interest).
- **Your own to-dos** (snoozable), and the cards and bank bonuses you want next, with what's in the way (5/24, bonus rules, a credit score you want first) and the earliest day to apply.
- **The best card for a purchase** by category, including rates earned only through the issuer's travel portal, and **estimated points** by program (airline and hotel programs each their own) at estimated values you can change.

## Everyday

- **A setup checklist** for new users (connect a bank, pick your main account, add paychecks and bills, set budgets).
- A dark interface that works on a phone as well as a desktop, with a tab bar at the bottom on phones, and it **installs as an app** on your iPhone's Home Screen. See [Deployment](/Runway/start/deployment/#on-your-phone).
- **Push notifications** (Settings → Notifications) on your phone or computer: a card payment coming up, the forecast getting low, a recurring payment that didn't show up, a large charge, syncing that keeps failing, or, for Churning, an annual fee or sign-up bonus deadline coming up, a card you planned to downgrade or close, a credit about to reset, or a card you planned that you can apply for now. Each alert is sent once. With sign-in, notifications are each person's own: their devices and what they're told about, which nobody else sees. Turn them off on a device from that device, or from the Devices list.
- **Backups** as a single file, restorable into either database (Settings → Advanced, or the command line). Settings shows what a backup holds before you restore it, and keeps a copy of what it replaces in the data directory. See [Deployment](/Runway/start/deployment/#backups-migration-and-postgres).
- **AI assistants** can read your data through Runway's [MCP endpoint](/Runway/using/mcp/) once you approve them (Settings → Advanced lists and revokes them), and, if you allow it, make some changes.
