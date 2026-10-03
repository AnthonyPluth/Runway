<script lang="ts">
  import { fmt0, pct } from "$lib/format";
  import { textOn } from "./look";
  import Tip from "./Tip.svelte";
  import type { BreakdownNode } from "./types";

  // Squarified treemap: blocks sized by value, a 2px gap between them, names inside the ones big enough to hold
  // them, in dark ink or white, whichever reads better on the block's color. Click a block (or press Enter on it) to
  // look inside. On a touch screen a block too small for its name shows its readout on the first tap, and opens on
  // the second.
  type Item = BreakdownNode & { color: string };
  let { items, total, onpick }: { items: Item[]; total: number; onpick: (c: Item) => void } = $props();

  let width = $state(0);
  const W = $derived(Math.max(300, width));
  const H = $derived(Math.round(Math.min(460, Math.max(260, W * 0.5))));

  type Cell = { c: Item; a: number };
  const rects = $derived.by(() => {
    const vals = items.filter((c) => c.value > 0);
    const scale = (W * H) / (total || 1);
    const out: (Cell & { x: number; y: number; w: number; h: number })[] = [];
    const worst = (row: Cell[], side: number) => {
      const s = row.reduce((a, c) => a + c.a, 0), max = Math.max(...row.map((c) => c.a)), min = Math.min(...row.map((c) => c.a));
      return Math.max((side * side * max) / (s * s), (s * s) / (side * side * min));
    };
    let x = 0, y = 0, w = W, h = H;
    let queue: Cell[] = vals.map((c) => ({ c, a: c.value * scale }));
    while (queue.length) {
      const side = Math.min(w, h);
      const row = [queue[0]];
      let i = 1;
      while (i < queue.length && worst([...row, queue[i]], side) <= worst(row, side)) { row.push(queue[i]); i++; }
      queue = queue.slice(i);
      const s = row.reduce((a, c) => a + c.a, 0);
      if (w >= h) {   // lay the row down the left side
        const rw = s / h;
        let yy = y;
        for (const r of row) { const rh = r.a / rw; out.push({ ...r, x, y: yy, w: rw, h: rh }); yy += rh; }
        x += rw; w -= rw;
      } else {        // along the top
        const rh = s / w;
        let xx = x;
        for (const r of row) { const rw = r.a / rh; out.push({ ...r, x: xx, y, w: rw, h: rh }); xx += rw; }
        y += rh; h -= rh;
      }
    }
    return out.map((r) => {
      const rx = r.x + 1, ry = r.y + 1, rw = Math.max(0, r.w - 2), rh = Math.max(0, r.h - 2);
      const name = r.c.name.length * 7 > rw - 16 ? r.c.name.slice(0, Math.max(3, Math.floor((rw - 16) / 7))) + "…" : r.c.name;
      return { c: r.c, rx, ry, rw, rh, fits: rw > 70 && rh > 34, name, ink: textOn(r.c.color) };
    });
  });

  let hover = $state<Item | null>(null), box = $state<HTMLDivElement | null>(null), at = $state({ x: 0, y: 0 });
  function show(e: MouseEvent, c: Item) {
    const r = box!.getBoundingClientRect();
    hover = c; at = { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  const pick = (e: KeyboardEvent, c: Item) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onpick(c); } };
  let touch = false;
  // A new level puts the readout away, and so does a tap outside the chart.
  $effect(() => { void items; hover = null; });
  $effect(() => {
    if (!hover || !box) return;
    const el = box;
    const away = (e: PointerEvent) => { if (!el.contains(e.target as Node)) hover = null; };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  });
  function tap(r: (typeof rects)[number]) {
    if (touch && !r.fits && hover?.name !== r.c.name) {
      hover = r.c; at = { x: (r.rx + r.rw / 2) * (width / W), y: (r.ry + r.rh / 2) * (width / W) };
      return;
    }
    onpick(r.c);
  }
</script>

<div class="relative" bind:this={box} bind:clientWidth={width}>
  <svg viewBox={`0 0 ${W} ${H}`} class="block h-auto w-full" role="group"
    aria-label={`Spending by ${items.filter((c) => c.value > 0).map((v) => v.name).slice(0, 5).join(", ")}`}>
    {#each rects as r (r.c.name)}
      <g role="button" tabindex="0" aria-label={`${r.c.name}: ${fmt0(r.c.value)}`}
        class="group cursor-pointer outline-none"
        onpointerdown={(e) => (touch = e.pointerType !== "mouse")} onclick={() => tap(r)} onkeydown={(e) => pick(e, r.c)}
        onmousemove={(e) => { if (!touch) show(e, r.c); }} onmouseleave={() => { if (!touch) hover = null; }}>
        <title>{r.c.name}</title>
        <rect x={r.rx} y={r.ry} width={r.rw} height={r.rh} rx="4" style:fill={r.c.color}
          class="transition-opacity group-hover:opacity-80 group-focus-visible:opacity-80 group-focus-visible:stroke-foreground group-focus-visible:stroke-2" />
        {#if r.fits}
          <text x={r.rx + 8} y={r.ry + 17} fill={r.ink} font-size="12.5" font-weight="600" class="pointer-events-none">{r.name}</text>
          <text x={r.rx + 8} y={r.ry + 32} fill={r.ink} font-size="12" class="pointer-events-none tabular-nums">{fmt0(r.c.value)}</text>
        {/if}
      </g>
    {/each}
  </svg>
  {#if hover}
    <Tip x={at.x} y={at.y} boxWidth={width} boxHeight={H * (width / W)} place="follow">
      <div class="text-muted-foreground">{hover.name}</div>
      <div class="text-[15px] font-semibold tabular-nums">{fmt0(hover.value)}</div>
      <div class="flex justify-between gap-3 text-muted-foreground"><span>of this view</span><span class="tabular-nums">{pct(hover.value / (total || 1))}</span></div>
    </Tip>
  {/if}
</div>
