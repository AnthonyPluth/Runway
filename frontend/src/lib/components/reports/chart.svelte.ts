// What the report charts share: axis ticks, month labels, following a finger, and loading a report while keeping
// the last one on screen.

/** Round numbers for an axis from min to max. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  const span = max - min || Math.abs(max) || 1, raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw)!;
  const start = Math.floor(min / step) * step, end = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = start; v <= end + step / 2; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}

/** "2026-09" → "Sep", or "Jan 26" to mark where a year starts. */
export function monthTick(m: string, withYear = false): string {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString("en-US", withYear ? { month: "short", year: "2-digit" } : { month: "short" });
}

/** A touch on the chart shows its readout, and dragging sideways moves it; dragging up or down still scrolls the
 * page. (Svelte's own touch handlers are passive, so they couldn't stop the page scrolling sideways.) */
export function scrub(el: Element, onMove: (clientX: number) => void) {
  let start: Touch | null = null;
  const down = (e: Event) => { start = (e as TouchEvent).touches[0]; onMove(start.clientX); };
  const drag = (e: Event) => {
    const t = (e as TouchEvent).touches[0];
    if (start && Math.abs(t.clientY - start.clientY) > Math.abs(t.clientX - start.clientX)) return;
    onMove(t.clientX);
    if (e.cancelable) e.preventDefault();
  };
  el.addEventListener("touchstart", down, { passive: true });
  el.addEventListener("touchmove", drag, { passive: false });
  return { destroy() { el.removeEventListener("touchstart", down); el.removeEventListener("touchmove", drag); } };
}

/** A report's data. Changing a control loads again but keeps the last answer on screen until the new one arrives
 * (as the classic page did), and a slow old answer never replaces a newer one. `loading` is true while a load is
 * out, so the last answer can be dimmed meanwhile rather than look current. */
export class Report<T> {
  data = $state<T | null>(null);
  error = $state<Error | null>(null);
  loading = $state(false);
  #seq = 0;
  constructor(private fetcher: () => Promise<T>) { this.load(); }
  load(): void {
    const n = ++this.#seq;
    this.loading = true;
    let p: Promise<T>;
    try { p = this.fetcher(); } catch (e) { p = Promise.reject(e); }
    p.then(
      (d) => { if (n === this.#seq) { this.data = d; this.error = null; this.loading = false; } },
      (e: Error) => { if (n === this.#seq) { console.error(e); this.error = e; this.loading = false; } });
  }
}
