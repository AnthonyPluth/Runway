// The forecast's `budget.skipped` reasons (runway/domain/forecast.py), in words a person can act on. A budget the
// recurring items already cover is left out on purpose (nothing to do); the others need something set up. A reason
// this app doesn't know yet is shown as the API sent it, so a new one is never dropped.
export type Skipped = { category: string; reason: string };
export type SkipGroup = { kind: "covered" | "setup" | "other"; title: string; hint: string; fix?: { href: string; label: string }; categories: string[] };

const ACCOUNTS = { href: "#setup/accounts", label: "Open account settings" };
const KNOWN: { starts: string; kind: SkipGroup["kind"]; title: string; hint: string; fix?: SkipGroup["fix"] }[] = [
  { starts: "a recurring", kind: "covered", title: "Already covered by recurring",
    hint: "A recurring bill or income item already counts these, so the budget isn't added on top." },
  { starts: "its account", kind: "setup", title: "Account isn't in the forecast",
    hint: "Their account isn't one the forecast follows, so their budgets can't be spent from it.", fix: ACCOUNTS },
  { starts: "its card isn't", kind: "setup", title: "Card isn't paid from a forecast account",
    hint: "Their card's payments don't come from an account in the forecast.", fix: ACCOUNTS },
  { starts: "its card's statement", kind: "setup", title: "Card statement is out of date",
    hint: "Their card's latest statement is missing or old, so its spending can't be placed.", fix: ACCOUNTS },
];

// The left-out categories grouped by reason, in the order the API listed them.
export function groupSkipped(skipped: Skipped[]): SkipGroup[] {
  const groups = new Map<string, SkipGroup>();
  for (const s of skipped) {
    let g = groups.get(s.reason);
    if (!g) {
      const k = KNOWN.find((x) => s.reason.startsWith(x.starts));
      const text = s.reason.trim();
      g = k ? { kind: k.kind, title: k.title, hint: k.hint, fix: k.fix, categories: [] }
        : { kind: "other", title: text ? text[0].toUpperCase() + text.slice(1) : "Left out", hint: "", categories: [] };
      groups.set(s.reason, g);
    }
    g.categories.push(s.category);
  }
  return [...groups.values()];
}
