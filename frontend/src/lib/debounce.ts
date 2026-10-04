// Waiting for a pause before acting (a save, a search), and only trusting the latest answer.

/** `fn`, a moment (`ms`) after the last call to `call`, with that call's arguments.
 * `flush` runs a waiting call now (nothing if none is waiting), `cancel` drops it, and `pending` says one is waiting. */
export function debounced<A extends unknown[]>(fn: (...args: A) => unknown, ms: number) {
  let timer: ReturnType<typeof setTimeout> | undefined, waiting: A | null = null;
  const run = () => {
    clearTimeout(timer); timer = undefined;
    const args = waiting;
    waiting = null;
    if (args) fn(...args);
  };
  return {
    call(...args: A): void { waiting = args; clearTimeout(timer); timer = setTimeout(run, ms); },
    flush: run,
    cancel(): void { clearTimeout(timer); timer = undefined; waiting = null; },
    get pending(): boolean { return waiting !== null; },
  };
}

/** For answers that can arrive out of order: each `begin()` returns a check that is true only until the next
 * `begin()` (or `cancel()`), so a slow old answer can see it was overtaken and step aside. */
export function latestOnly() {
  let n = 0;
  return {
    begin(): () => boolean { const mine = ++n; return () => mine === n; },
    cancel(): void { n++; },
  };
}
