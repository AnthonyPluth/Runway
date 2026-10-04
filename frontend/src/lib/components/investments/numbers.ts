// How the Investments page writes returns and gains: signed, with a real minus (−) for a loss, as everywhere in Runway.
import { fmtSigned } from "$lib/format";

/** "+4.2%", "−1.0%" for a loss, "0.0%" (never a negative zero), or "—" for nothing (or not a number). */
export function pct(x: number | null | undefined, digits = 1): string {
  if (x == null || !isFinite(x)) return "—";
  const shown = Math.abs(x * 100).toFixed(digits);
  return Number(shown) === 0 ? `${shown}%` : `${x > 0 ? "+" : "−"}${shown}%`;
}
/** A percentage without its sign, for "ahead by 1.2%": "1.2%", "0.0%", or "—". */
export const pctAbs = (x: number | null | undefined, digits = 1) => (x == null || !isFinite(x) ? "—" : pct(Math.abs(x), digits).replace(/^\+/, ""));
/** Gains are green (text-good), losses a soft red (text-loss); nothing (or what rounds to nothing) keeps the ordinary color. */
export const gainCls = (x: number | null | undefined) => (x == null ? "" : x > 0 ? "text-good" : x < 0 ? "text-loss" : "");
/** "+$1,234.00", "−$12.00" for a loss, "$0.00" for what rounds to nothing, or "—". */
export const signed = (x: number | null | undefined): string => (x == null || !isFinite(x) ? "—" : fmtSigned(x));
/** Share counts: up to 4 decimals. */
export const qty = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 4 });
