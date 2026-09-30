# Features

The [README](../README.md) has the short list. This is what each part does and where its settings live.

Runway's pages, in the sidebar (or the tab bar on a phone): **Overview**, **Transactions** (All and To review), **Budget** (Budget and Bills & income), **Reports**, then **Net worth** (Summary, Investments, Equity and Retirement) and **Churning**, and **Settings** at the bottom.

Settings has eight tabs: **Accounts**, **Bank connections** (SimpleFIN and Plaid), **Categories**, **Rules**, **Browser extension** (Amazon, Target, Costco and Carta), **Services** (OpenRouter, Realie, Finnhub and Logo.dev), **Notifications**, and **Advanced** (MCP keys, backup and restore, and the version). Until a bank is connected, Settings opens on Bank connections.

## Cash-flow forecast

On the Overview page.

- **Day-by-day projection** of your primary account for 30 days to 6 months, with the lowest point called out in plain language ("Checking stays above $2,084 for the next 120 days").
- **Credit cards paid the way you pay them:** each card's statement balance comes out of checking on its due date. Statement balances, closing dates, due dates and minimum payments come straight from the card issuer through Plaid (Liabilities), so there's nothing to set up by hand. You can still correct a statement figure.
- **Future statements** are estimated from each card's average spending over its last three statements.
- **Business days:** a payment due on a weekend or bank holiday lands on the next business day, and a paycheck on the business day before (Federal Reserve holiday calendar).
- **One-off edits:** click any upcoming amount to change it for that date only.
- **This month:** spending so far against the same point last month, the budgets closest to their limit, and the latest transactions.

## Bank connections

Under Settings → Bank connections, with each account's source chosen under Settings → Accounts.

- **SimpleFIN or Plaid for each account.** Link a bank or card through Plaid, match its accounts to the ones you already have, and pick where each one's balance and transactions come from. Switching keeps your history, categories and recurring matches: transactions both providers have are matched up so nothing is counted twice.
- Accounts only Plaid can reach can be added on their own, and a card linked through Plaid gets its statements from the issuer whichever provider its transactions come from.
- **Automatic sync** once a day at 7 AM (Runway's time zone, America/Chicago by default), SimpleFIN and Plaid together, late enough for overnight ACH. Opening Runway only catches up a day's sync that was missed. At 6:30 AM Runway asks Plaid to fetch from the banks (Plaid's Transactions Refresh add-on), so the 7 AM sync is current. The Sync button syncs SimpleFIN whenever you press it; Plaid, whose quota is small, is asked once a day only, except by a connection's own Sync button.

## Bills & income

On Budget → Bills & income.

- Paychecks, mortgage, bills and subscriptions: weekly, every two weeks, twice a month, monthly, quarterly, twice a year, yearly, or on specific dates (property tax on April 15 and October 15, say).
- Transactions are **matched automatically** to their recurring item, and Runway **flags payments that didn't happen**.
- Suggestions for recurring items it spots in your history.

## Transactions and categorization

- **Rules** (Settings → Rules) with conditions and actions: when the merchant contains, is or starts with some text, the amount is in a range, the money goes out or comes in, or it's in a particular account, then set a category, rename the merchant, split it by percentages ("Costco: 70% Groceries, 30% Household") or put it in Review. The most specific rule wins, and a preview shows what a rule would match before you save it. When you pick a category, Runway asks whether to use it for that merchant from now on, including after you apply an AI suggestion, so nothing writes a rule behind your back. Built-in heuristics handle card payments and sweeps.
- **Optional AI categorization** through OpenRouter (key under Settings → Services), guided by examples of how you've categorized before and shown with confidence scores. Confident answers can be applied during sync; the rest wait for you, and it can propose new categories when nothing fits. Every AI call is logged.
- **Split transactions:** a $100 run to Target can be $60 Groceries and $40 Shopping. Budgets, reports and category filters count each part on its own; pick a single category again and the transaction goes back together.
- **Amazon, Target and Costco orders:** a small browser extension (installed from Settings → Browser extension) reads your orders with the sign-in already in your browser, and Runway splits each card charge by what you bought: tax and shipping shared out, each Amazon shipment matched to its own items. Items are categorized by the AI if you've set it up, and a category you pick for an item sticks for next time. See [extension/README.md](../extension/README.md).
- **Bulk editing:** tick transactions (shift-click for a range) to give them a category, rename their merchant or mark them reviewed at once.
- **Categories** (Settings → Categories) have one level of subcategories, each with an emoji and a color. Anything uncategorized waits on the To review tab; there is also search and filtering. The list is grouped by day with each day's total, and loads more as you scroll.
- **Merchant logos** for merchants Plaid knows, downloaded once from Plaid and served by Runway itself. With a free Logo.dev publishable key (Settings → Services), merchants Plaid has no logo for get one from Logo.dev by their website (Plaid's, or for about 95 big names like Target, Amazon and Walmart, one Runway knows): downloaded during a sync, re-checked monthly, served by Runway. The same key gives each bank or card its logo by the institution's name; without it, accounts show a letter.

## Budget and reports

- Monthly budgets per category with pace markers; click any amount to jump to the transactions behind it.
- **Rollover**, budget by budget: what's left at the end of a month is added to the next (going over isn't carried).
- **Reports** has five views: **Cash flow** (a Sankey chart of where each month's money went), **Over time** (spending by category, merchant or account over 6, 12 or 24 months, with each one's change from last month and a year ago), **Merchants** (ranked by what you spent, each with its months and transactions), **Income vs spending** (with your savings rate) and **Breakdown** (a treemap you click into, from categories to subcategories to merchants to the transactions behind them).

## Investments

On Net worth → Investments.

- Holdings and performance for brokerage and retirement accounts, modeled on Ghostfolio: time-weighted return, a comparison with the S&P 500, gain per holding and allocation.
- **Near-real-time prices** while the market is open, with each holding's gain today. With a free [Finnhub](https://finnhub.io) key (Settings → Services) they become real trades, pushed the moment they happen for up to 50 of your stocks and ETFs. Runway holds one connection to Finnhub for the whole app and your browser never sees the key. Yahoo takes over for anything else, or if Finnhub can't be reached.
- Positions from SimpleFIN, or optionally from Plaid for accounts SimpleFIN only knows the balance of. Accounts that neither can see into, like some 401(k)s, can be **tracked by hand**: enter shares and your contribution split, and Runway invests each new deposit accordingly.
- Editable cost basis, per share.

## Retirement

On Net worth → Retirement.

- A retirement planner that projects your investments year by year to the age you plan for, across 1,000 simulated markets, with the chance your money lasts. Its assumptions (spending, saving, return, Social Security, pensions, one-off events, selling a home) start from your own numbers and keep whatever you change; **Start over** (after a confirmation) goes back to Runway's figures. Until you enter your own dates the results are marked as a sample, and with nothing invested yet the tab points you to Investments instead. It plans from your investments only, in today's dollars and before tax.

## Net worth and equity

- **Summary:** every account plus homes, vehicles and anything else you own, minus cards and loans, recorded daily. Optional automated home values through Realie (key under Settings → Services; each home is looked up once a week at most, within the free tier).
- **Equity:** stock options (ISO/NSO), RSUs, restricted stock and shares, each with its vesting schedule (cliff, monthly or quarterly), exercise price and exercises. Runway shows what has vested and what's still to come at each company's latest share price, and counts the vested part in net worth. Enter grants by hand, or read them from **Carta** with the browser extension (with your Carta sign-in, like Amazon and Target) or with Carta's Portfolio API if Carta approves your app.

## Churning

Credit cards and bank accounts opened for their sign-up bonuses, for you and your partner, on three tabs: Cards, Benefits and Bank bonuses.

- **5/24** per person (authorized-user, business and product-changed cards left out) with the day each card stops counting.
- **Annual fees** coming up (keep, downgrade or close?), or your plan for each card: keep it and it stops asking; downgrade or close it and you're reminded before the fee, with a Done that updates the card.
- **Bonus spending and direct deposits** followed from linked accounts, and when a bonus can be earned again by each bank's commonly reported rule (a rule of thumb, overridable).
- **Card benefits and credits**, what's left this period, and the annual fee net of the ones you use.
- **Bank accounts:** safe-to-close days and fee-waiver reminders, and bank bonus money per year (usually reported as interest).
- **Your own to-dos** (snoozable), and the cards and bank bonuses you want next, with what's in the way (5/24, bonus rules, a credit score you want first) and the earliest day to apply.
- **The best card for a purchase** by category, including rates earned only through the issuer's travel portal, and **estimated points** by program (airline and hotel programs each their own) at estimated values you can change.

## Everyday

- **Autosave everywhere:** there are no Save buttons.
- **A setup checklist** for new users (connect a bank, pick your main account, add paychecks and bills, set budgets).
- A dark interface that works on a phone as well as a desktop, with a tab bar at the bottom on phones, and it **installs as an app** on your iPhone's Home Screen. See [Deployment](deployment.md#on-your-phone).
- **Push notifications** (Settings → Notifications) on your phone or computer: a card payment coming up, the forecast getting low, a recurring payment that didn't show up, a large charge, syncing that keeps failing, or, for Churning, an annual fee or sign-up bonus deadline coming up, a card you planned to downgrade or close, a credit about to reset, or a card you planned that you can apply for now. Each alert is sent once.
- **Backups** as a single file, restorable into either database (Settings → Advanced, or the command line). See [Deployment](deployment.md#backups-migration-and-postgres).
- **AI assistants** can read your data through a read-only [MCP server](mcp.md) (key under Settings → Advanced).
