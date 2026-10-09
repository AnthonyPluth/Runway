// Who set a transaction's category (`category_source`), in words: "Set by a rule", "Suggested by the AI".
const SOURCES: Record<string, string> = {
  manual: "you", ai: "the AI", rule: "a rule", history: "your past choices", memory: "your past choices", retail: "the store order",
  department: "the store order", auto: "Runway", recurring: "its recurring item",
};

/** Who `source` is, in words ("" when there's none). An unknown one is said as it is. */
export const sourceLabel = (source: string | null | undefined): string => (source ? SOURCES[source] ?? source : "");
