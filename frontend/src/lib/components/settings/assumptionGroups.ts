// Every assumption and explanation Runway used to scatter across its pages, in one place: Settings → Assumptions. Pages link
// to their group (#setup/assumptions/<id>) instead of explaining themselves. Keep it in step with docs/…/using/features.md.

export interface AssumptionGroup { id: string; title: string; items: string[] }

/** The page's one disclaimer, shown once at the top. */
export const DISCLAIMER = "Runway’s figures are estimates worked out from your own data, for your own planning. They are not investment, tax or financial advice.";

export const ASSUMPTION_GROUPS: AssumptionGroup[] = [
  {
    id: "forecast", title: "Forecast", items: [
      "The forecast projects your main account day by day, from 30 days to 6 months ahead. Choose the account, and the default length, from the account’s name above the balance on Overview, or with “Use for the forecast” in Settings → Accounts.",
      "It starts from the bank’s balance plus whatever is pending on the account, money in and out. Pending money a bank already takes out of its balance isn’t taken out twice, and a pending paycheck that matches its recurring item counts as paid. SimpleFIN’s pending transactions count only from the last 14 days, the ones a sync re-reads; Plaid’s all count, as Plaid removes the ones that are no longer pending.",
      "Paychecks and bills come from Bills & income. A payment due on a weekend or bank holiday lands on the next business day, and a paycheck on the business day before.",
      "Each credit card’s payment comes out of the account on its due date, in full unless you set the account to pay less. Statements come from the card issuer through Plaid where the bank shares them; otherwise enter the latest one in Settings → Accounts. A statement you entered counts until about a month after it closed, and Plaid’s wins when both exist.",
      "Future statements are estimated from the card’s average over its last three statements. For the statement in progress, it’s what has been charged so far (pending charges counted as in the starting balance) plus the average’s share of the days left before it closes. Refunds and payments above a statement come off the next one. Recurring items the average can’t stand for, like a yearly premium or a new subscription, are added on their own dates instead.",
      "Everyday spending (card swipes, cash and other spending that isn’t a bill or a transfer) is left out unless you turn it on for the account. When it’s on, the average from the last 90 days is spread evenly over each day. It leaves out payments to recurring items and single payments over $1,000.",
      "“If you stick to your budget” is a dashed line that spends each budget on its account or card in place of the estimated card statements. A budget’s recurring payments count toward it, one that rolls over also spends what it carried into the month, and a budget on a card that isn’t paid from the forecast account is left out. A card with no statement yet still counts: until one says when its cycle closes, it’s taken to close at each month’s end and be paid 25 days later, the way the card is set to be paid.",
      "A churning card’s annual fee is a charge on the card in the month it was opened, from its first anniversary on, and comes out with that statement’s payment. There’s none for a card that’s closed, planned to be closed or changed before the fee, or without a fee.",
      "With more than one account in the forecast, a recurring transfer from one to another counts only as money out unless you add the matching money in on the other account as its own recurring item. Issuers that post a statement a day or two after it closes have those days’ charges counted toward the next statement.",
      "Card lines on Overview show what the card owes now and its average per statement, taken over the last few statements; a statement you corrected is marked as set by you.",
    ],
  },
  {
    id: "budget", title: "Budget & spending", items: [
      "Budgets are monthly, per category, and repeat every month. A budget set to roll over adds what’s left at the end of a month to the next; going over isn’t carried.",
      "Spending in Budget and Reports counts every category except transfers and income, plus money out you haven’t categorized. A month with more money back than out counts as no spending. Transactions split across categories count each part on its own.",
      "Reports compare each month with last month and a year ago. The current month is still going, so its figures are partial. Your savings rate is the share of money in that wasn’t spent.",
      "Bills & income: a transaction is matched to its item by merchant text, on its account and the same way the money moves, whatever the amount unless you set an amount range. Payments for one date are added up. If money in, or money out that paid less than half, comes to less than a fixed amount by more than 10%, the rest is still expected until the payment’s window closes (4 days either side for twice a month, 6 for monthly); after that nothing is counted late.",
      "When a fixed amount’s last few payments all came to something else, the item offers what they came to. The forecast keeps the amount you set until you take it. Payments that look like they repeat are suggested from your history; “Not recurring” hides one, and it can be restored.",
      "Runway flags a payment that didn’t happen only when nothing came.",
    ],
  },
  {
    id: "transactions", title: "Transactions & categories", items: [
      "Banks come in through SimpleFIN (about six months of history on the first sync) or Plaid (up to two years), synced together once a day at 7 AM in Runway’s time zone. Transactions both providers have are matched up, so nothing is counted twice.",
      "A bank’s text is tidied to the merchant: card processors’ prefixes, store numbers and the words a bank adds after the merchant come off. Big brands get their own name when the bank’s text starts with it (“AMZN Mktp US” is Amazon, “WM Supercenter” Walmart), but not a merchant Plaid already named, nor a name you or a rule gave it. The bank’s full text stays under the transaction’s details, where “Use the bank’s name” puts it back, for one transaction or for all of that brand’s from now on.",
      "Rules act on a transaction when it matches everything they ask for, and the most specific rule wins. They run on new transactions as they sync; Apply runs one over past transactions too, without changing a category you picked yourself.",
      "Categories have one level of subcategories. Anything categorized Ignore (or one of its subcategories) stays off the All tab until you choose Show on its “N ignored” line.",
      "Amazon, Target and Costco orders are read by the browser extension with the sign-in already in your browser and sent only to Runway. Each card charge is split by what you bought, with tax and shipping shared out. Items take the transaction’s category until you pick one, or until the AI does if it’s set up; a category you pick for an item sticks for next time.",
      "Merchant logos come from Plaid. With a Logo.dev key, merchants Plaid has none for get one by their website, or by name when there’s no website; adding the key fetches the past year’s, and each sync fetches new merchants’. Runway downloads and serves the logos itself, so your browser never contacts Logo.dev, and Logo.dev sees only merchants’ websites and names. The same key gives banks their logos; with the secret key, a logo is kept only when the brand clearly is that merchant.",
    ],
  },
  {
    id: "networth", title: "Net worth", items: [
      "Net worth is every account plus homes, vehicles and anything else you own, minus cards and loans, recorded daily. An account turned off with “Count in net worth” stays in transactions, the forecast and Investments, and only drops out of these totals.",
      "The change over 30 days, 90 days or a year is measured from the latest snapshot at least that old. Snapshots are saved on the days the numbers are looked at, so after a gap the change can cover longer than its label; when it’s more than a few days longer, the page says since when. With no snapshot that old, no change is shown.",
      "A loan with an interest rate and a monthly payment is paid down month by month from its last balance, so equity grows even when the bank doesn’t send a new balance. The rate and payment come from the lender through Plaid, from what you enter in Settings → Accounts, or, with a rate but no payment, from the typical month’s payments into the loan account lately. Without a rate or payment, the balance stays as the bank last reported it.",
      "With a Realie key, each home’s value is looked up once a week at most, within the free tier’s 25 lookups a month. Once Realie has valued a home with a usable address and you keep it updated weekly, its value comes only from Realie (Update from Realie, or weekly); vehicles, other assets, homes Realie can’t value and homes you don’t keep updated are valued by hand.",
    ],
  },
  {
    id: "investments", title: "Investments", items: [
      "Return is time-weighted, so deposits and withdrawals don’t count as gains, and it’s compared with the S&P 500 over the same period. Allocation and gain are per holding.",
      "Positions come from SimpleFIN, or from Plaid for accounts SimpleFIN only knows the balance of. SimpleFIN sends no activity, so those accounts are rebuilt from the daily position snapshots Runway saves; before the first snapshot, history is estimated by assuming you held the same positions, and the chart marks it as estimated.",
      "Accounts neither can see into, like some 401(k)s, can be tracked by hand: each fund’s ticker and shares (or its value, if it has no ticker) and your contribution split, from your plan’s website. Runway invests each new deposit accordingly.",
      "Prices update near real time while the market is open. With a Finnhub key they’re real trades for up to 50 of your stocks and ETFs; Yahoo supplies everything else (price history, splits, mutual funds, tickers over the limit) and live prices when there’s no key. Runway holds one connection to Finnhub for the whole app, your browser never sees the key, and Finnhub learns only which tickers you hold. Finnhub’s free plan is for personal use.",
      "A holding without a cost basis is left out of the total gain. Cost basis is edited as the price paid per share, your average if you bought at different prices; Runway multiplies it by the shares you hold, and a box left empty goes back to what the institution reports.",
    ],
  },
  {
    id: "retirement", title: "Retirement", items: [
      "The planner projects your investments year by year to the age you plan for, across 1,000 simulated markets, and shows the chance your money lasts. It plans from your investments only. Taxes aren’t modelled, and cash isn’t included. Until you enter your birth year and retirement age, the results are a sample from default assumptions, shown muted.",
      "Until you enter your own dates, the results are a sample based on default assumptions. Each assumption starts from your own numbers and keeps whatever you change; “Start over” goes back to Runway’s figures.",
      "The projection runs once in today’s dollars. Future dollars only grows each year’s figure by inflation for display, so the curve bends upward but the odds, run-out age, sales and payments are identical; the switch is remembered in that browser. What you enter (spending, savings, Social Security and other income, one-time events) is in today’s dollars and keeps pace with inflation. Loan payments and balances are fixed dollar amounts, so in today’s dollars they shrink each year.",
      "Returns are after inflation. Each of the 1,000 runs draws every year’s return around your averages; “ups and downs” is the yearly volatility, how far a year typically strays (a stock-heavy portfolio is about 15%, a balanced one about 10%). Enter spending as what you’d withdraw before tax.",
      "Runway’s spending figure is the average of the last six full months: every category except transfers and income, plus uncategorized money out. With less history than that, it’s the average of the full months Runway has transactions for; the month history starts in counts only when it starts on the 1st. Runway’s saving figure is what went into your investments in the last 12 months, rollovers and lump sums included, and only goes back as far as your investment history; change it if that isn’t what you usually save. A spending figure you type yourself is taken as is.",
      "Savings stop at each person’s retirement; spending comes from the investments only once everyone has retired; until then the pay of whoever’s still working is assumed to cover living costs.",
      "Income in retirement (Social Security, a pension) is a yearly amount in today’s dollars, like the estimate at ssa.gov/myaccount. A one-time event (a home purchase, college, a wedding, an inheritance) is money in or out in one year.",
      "Home equity, other assets and vested equity are shown on top of the investments until sold into the plan; they pay for retirement only once sold, and are never counted twice. Tick one to sell it into your investments that year, say to downsize. A sale can’t be in the past; a sale year that has passed counts as this year. Sale proceeds are before selling costs (often 6–8% of a home’s price) and tax.",
      "An asset’s value grows by its yearly change from Net worth; with none set (and for equity, at today’s share price) it keeps pace with inflation. Loans are paid down on their terms; without an interest rate, today’s balance is used, and a payment that doesn’t cover the interest never pays the loan down. Equity counts what will have vested by then, including grants that haven’t vested yet: each grant goes on vesting on its schedule, from its vesting start or, without one, its grant date. Where Carta reported what’s vested, it goes on from that figure, and the rest vests as the schedule vests its rest, so it’s all vested when the schedule says. Options that expire later are taken as exercised before they do (what vested up to their expiration, net of the exercise price); options that have already expired count only the shares exercised from them. Vehicles lose value, so the plan leaves them out; a loan against one is still paid until it’s paid off. A loan against nothing (a student or personal loan) is in the plan as a debt: paid down on its terms, its payment treated like any other loan’s, and never sold.",
      "A loan payment found in spending comes off once the loan is paid off (from the following year) or its asset sold (from that year). One found paid as a transfer (transfers naming the lender or the loan, not just ones of the same size) is added to retirement spending until then. One Runway can’t find either way is left as it is. A payment counts as found when money out within 10% of it shows up in at least 4 of the last 6 months, or, with less history than that, in every full month Runway has (at least 2, or 1 when it names the lender). Ones naming the lender win, and may be up to 1.5× the payment for mortgage escrow.",
    ],
  },
  {
    id: "equity", title: "Equity", items: [
      "Stock options, RSUs, restricted stock and shares are entered by hand, or read from Carta with the browser extension (Settings → Connections) or with Carta’s Portfolio API if Carta approves your app.",
      "Only what has vested counts toward net worth, at each company’s latest share price (its 409A value, for a private company). What’s still to vest is shown at today’s share prices.",
      "Carta’s Portfolio API works only for apps Carta approves; an app made in its developer portal is a Playground app, with dummy data, until Carta grants production access.",
    ],
  },
  {
    id: "churning", title: "Churning", items: [
      "Churning tracks the cards and bank accounts you open for sign-up bonuses, for you and your partner: your 5/24 count, annual fees, bonus spending and when you can apply again.",
      "5/24 is per person, with authorized-user, business and product-changed cards left out, and each card counts until the day it falls off, closed or not. Banks don’t say when a card was opened, so a card found on your accounts is dated by its first transaction in Runway and marked “on or before”; change it to the real day, since 5/24 and the bonus rules depend on it.",
      "A card’s annual fee posts in the month it was opened. Planned to keep, it stops asking; planned to downgrade or close, you’re reminded before the fee (left blank, the day before it posts). The fee is also in the forecast, unless you plan to close or downgrade the card first.",
      "Linked to a Runway account, deposits categorized as income (or that look like payroll) count as direct deposits, and purchases as debit transactions. Banks decide what counts, so check the offer’s terms.",
      "When a bonus can be earned again follows each bank’s commonly reported rule (hover a card’s “Bonus again” to see it), a rule of thumb: banks change them and each offer has its own terms, so confirm with the bank before you apply, and set your own date on a card when you know better.",
      "Point values are estimates of what travelers commonly report, not official values; change them to match how you redeem. Return is points per dollar times what you say a point is worth. An estimated balance is yours plus what your linked cards earned since, at their normal rates; what a card earned this year is estimated from its spending, earning rates and bonuses.",
      "A card’s cost is its annual fee less the benefits you’ll use; a benefit marked not counted is left out. Lounge access and perks are on all year; credits count as used until they reset.",
      "Banks usually report account bonuses as interest on a 1099-INT, so they’re usually taxable. The yearly totals are for your records, not tax advice.",
    ],
  },
  {
    id: "ai", title: "AI", items: [
      "AI categorization goes through OpenRouter. Only the date, amount, merchant and account type of each transaction are sent, and every request is logged under AI activity. Categorizing an order’s items sends only their names and prices. Categorizing uses OpenRouter’s free-models router (openrouter/free) unless you pick another model, and only providers that don’t keep or train on what they’re sent (if none is available, nothing is sent and the error says so); free models sometimes answer in a format Runway can’t read, and then the error says so and a paid model fixes it.",
      "Card lookups use their own model, Claude Haiku 4.5 by default, because it calls OpenRouter’s web search reliably and costs little. Change either model under Settings → Connections → AI.",
      "Nothing changes until you apply a suggestion, except that confident answers can be applied during sync if you turn that on.",
      "“Fill in the rest with AI” on a card searches the web first by default, preferring the bank’s own page, and lists the pages it used; only the bank and the card’s name are searched for. The search costs extra on top of the model: about $4 per 1,000 results with OpenRouter’s own engine, at most 10 results a card (up to about 4¢), plus the model’s tokens. Turned off, it answers from the model’s memory, which costs less but is often out of date. Check what it suggests before saving.",
      "Assistants connected over MCP read your accounts, transactions, budget, reports, net worth, orders and churning, and never see your bank connections, settings or backups. They can change churning or categorize only if you turn that on, can’t delete or touch accounts or settings, and stop at once when you turn it off.",
      "Connect an assistant by adding Runway’s MCP address to it and approving it under Settings → Advanced when it asks: for Claude Code, claude mcp add --transport http runway followed by the address; for Claude on the web or desktop, Settings → Connectors → Add custom connector.",
    ],
  },
];
