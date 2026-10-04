// What the report charts share: month labels, and loading a report while keeping the last one on screen.

/** "2026-09" → "Sep", or "Jan 26" to mark where a year starts. */
export function monthTick(m: string, withYear = false): string {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString("en-US", withYear ? { month: "short", year: "2-digit" } : { month: "short" });
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
