// Where home values come from. Today that's Realie (runway/realie.py); older values may still say RentCast, the
// provider Runway used before. If the provider changes again, this is the one place in the app that names it.
import { fmt0, fmtDate } from "$lib/format";

export const HOME_VALUES = {
  refresh: "Update from Realie",
  refreshTitle: "Runway looks each home up once a week at most",
  auto: "Update from Realie weekly",
  nextLookup: (day: string) => `Next Realie lookup ${fmtDate(day)}`,
  lookups: (used: number, limit: number) => `${used} of ${limit} free lookups used this month`,
  refreshed: (r: { value: number; low: number | null; high: number | null }) =>
    `Realie estimate: ${fmt0(r.value)}${r.low && r.high ? ` (range ${fmt0(r.low)}–${fmt0(r.high)})` : ""}`,
};

/** "Realie estimate", "RentCast estimate" (older values) or "Your estimate", for where a value came from. */
export const valueSource = (source: string | null | undefined) =>
  source === "realie" ? "Realie estimate" : source === "rentcast" ? "RentCast estimate" : "Your estimate";
