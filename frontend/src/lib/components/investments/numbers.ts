// How the Investments page writes returns and gains, as in the classic app.
import { fmt } from "$lib/format";

/** "+4.2%", "−1.0%", "0.0%" (never "−0.0%"), or "—". */
export function pct(x: number | null | undefined, digits = 1): string {
  if (x == null) return "—";
  const shown = Math.abs(x * 100).toFixed(digits);
  return Number(shown) === 0 ? `${shown}%` : `${x > 0 ? "+" : "−"}${shown}%`;
}
/** Gains are green; losses stay the ordinary text color (red would read as an error). */
export const gainCls = (x: number | null | undefined) => (x != null && x > 0 ? "text-emerald-500" : "");
/** "+$1,234.00", "−$12.00" or "—". */
export const signed = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : "−"}${fmt(Math.abs(x))}`);
/** Round axis steps (1, 2, 2.5, 5 × 10ⁿ) covering min…max. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  const span = max - min || Math.abs(max) || 1, raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw)!;
  const start = Math.floor(min / step) * step, end = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = start; v <= end + step / 2; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}

/** Dragging sideways across a chart moves its readout; dragging up or down still scrolls the page. (Svelte's own
 *  touch handlers are passive, so they couldn't stop the page scrolling sideways.) */
export function sideways(el: Element, onMove: (clientX: number) => void) {
  let start: Touch | null = null;
  const down = (e: Event) => { start = (e as TouchEvent).touches[0]; };
  const drag = (e: Event) => {
    const t = (e as TouchEvent).touches[0];
    if (start && Math.abs(t.clientY - start.clientY) > Math.abs(t.clientX - start.clientX)) return;
    onMove(t.clientX);
    if (e.cancelable) e.preventDefault();
  };
  el.addEventListener("touchstart", down, { passive: true });
  el.addEventListener("touchmove", drag, { passive: false });
  return {
    update(next: (clientX: number) => void) { onMove = next; },
    destroy() { el.removeEventListener("touchstart", down); el.removeEventListener("touchmove", drag); },
  };
}

/** Share counts: up to 4 decimals. */
export const qty = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 4 });
