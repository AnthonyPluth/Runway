<script lang="ts">
  import { fmt, fmt0 } from "$lib/format";
  import Tip from "./Tip.svelte";
  import type { BreakdownNode } from "./types";

  // Squarified treemap: blocks sized by value, a 2px gap between them, names inside the ones big enough to hold
  // them. Click a block (or press Enter on it) to look inside.
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
      return { c: r.c, rx, ry, rw, rh, fits: rw > 70 && rh > 34, name };
    });
  });

  let hover = $state<Item | null>(null), box = $state<HTMLDivElement | null>(null), at = $state({ x: 0, y: 0 });
  function show(e: MouseEvent, c: Item) {
    const r = box!.getBoundingClientRect();
    hover = c; at = { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  const pick = (e: KeyboardEvent, c: Item) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onpick(c); } };
</script>

<div class="relative" bind:this={box} bind:clientWidth={width}>
  <svg viewBox={`0 0 ${W} ${H}`} class="block h-auto w-full" role="group"
    aria-label={`Spending by ${items.filter((c) => c.value > 0).map((v) => v.name).slice(0, 5).join(", ")}`}>
    {#each rects as r (r.c.name)}
      <g role="button" tabindex="0" aria-label={`${r.c.name}: ${fmt(r.c.value)}`}
        class="group cursor-pointer outline-none"
        onclick={() => onpick(r.c)} onkeydown={(e) => pick(e, r.c)}
        onmousemove={(e) => show(e, r.c)} onmouseleave={() => (hover = null)}>
        <rect x={r.rx} y={r.ry} width={r.rw} height={r.rh} rx="4" style:fill={r.c.color}
          class="transition-opacity group-hover:opacity-80 group-focus-visible:opacity-80 group-focus-visible:stroke-foreground group-focus-visible:stroke-2" />
        {#if r.fits}
          <text x={r.rx + 8} y={r.ry + 17} fill="#0e1116" font-size="12.5" font-weight="600" class="pointer-events-none">{r.name}</text>
          <text x={r.rx + 8} y={r.ry + 32} fill="#0e1116" opacity="0.8" font-size="12" class="pointer-events-none tabular-nums">{fmt0(r.c.value)}</text>
        {/if}
      </g>
    {/each}
  </svg>
  {#if hover}
    <Tip x={at.x} y={at.y} boxWidth={width} boxHeight={H * (width / W)} place="follow">
      <div class="text-muted-foreground">{hover.name}</div>
      <div class="text-[15px] font-semibold tabular-nums">{fmt(hover.value)}</div>
      <div class="flex justify-between gap-3 text-muted-foreground"><span>of this view</span><span class="tabular-nums">{Math.round((hover.value / total) * 100)}%</span></div>
    </Tip>
  {/if}
</div>
