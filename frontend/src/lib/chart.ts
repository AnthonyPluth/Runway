// What every chart shares: round axis ticks, the scales from data to pixels, and following a finger across a chart.

/** Round numbers for an axis (1, 2, 2.5, 5 × 10ⁿ steps) covering min…max. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  const span = max - min || Math.abs(max) || 1, raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw)!;
  const start = Math.floor(min / step) * step, end = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = start; v <= end + step / 2; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}

/** The vertical axis for `ticks` (lowest first): y0 and y1 are its ends, and y(v) the pixel row of a value, with y1
 * at `top` and y0 `height` below it. */
export function yScale(ticks: number[], top: number, height: number) {
  const y0 = ticks[0], y1 = ticks[ticks.length - 1];
  return { y0, y1, y: (v: number) => top + (1 - (v - y0) / (y1 - y0 || 1)) * height };
}

/** The pixel column of point i, with points `first` to `last` spread evenly from `left` across `width`. */
export const xScale = (first: number, last: number, left: number, width: number) =>
  (i: number) => left + ((i - first) / Math.max(1, last - first)) * width;

/** Dragging sideways across a chart moves its readout; dragging up or down still scrolls the page. (Svelte's own
 * touch handlers are passive, so they couldn't stop the page scrolling sideways.) With `atStart`, the touch itself
 * also moves it. Which of the two a drag is gets decided once, from its first few pixels: a drag that has gone sideways
 * keeps following the finger however far it drifts up or down, rather than freezing on a day. */
const LOCK_PX = 6;
function touchDrag(el: Element, onMove: (clientX: number) => void, atStart: boolean) {
  let start: Touch | null = null, vertical = false, locked = false;
  const down = (e: Event) => {
    start = (e as TouchEvent).touches[0]; vertical = locked = false;
    if (atStart) onMove(start.clientX);
  };
  const drag = (e: Event) => {
    const t = (e as TouchEvent).touches[0];
    if (start && !locked) {
      const dx = Math.abs(t.clientX - start.clientX), dy = Math.abs(t.clientY - start.clientY);
      if (Math.max(dx, dy) >= LOCK_PX) { locked = true; vertical = dy > dx; }
    }
    if (vertical) return;
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

/** A Svelte action: a sideways drag moves the readout (a plain touch doesn't; the tap's mouse events do that). */
export const sideways = (el: Element, onMove: (clientX: number) => void) => touchDrag(el, onMove, false);

/** A Svelte action: a touch shows the readout where it lands, and dragging sideways moves it. */
export const scrub = (el: Element, onMove: (clientX: number) => void) => touchDrag(el, onMove, true);
