<script lang="ts">
  import { fmt, fmt0 } from "$lib/format";
  import Tip from "./Tip.svelte";
  import type { Cashflow, CashflowNode } from "./types";

  // The month as a Sankey: money in on the left flows into the month, and out to spending on the right (then to
  // subcategories). Small spending (under 2%) is gathered into "Everything else". Hover a band or a bar for its
  // amount and share. It never gets narrower than 720px; on a phone it scrolls sideways inside its card.
  let { cf, monthName }: { cf: Cashflow; monthName: string } = $props();

  type Role = "in" | "out" | "hub" | "neutral";
  type Node = CashflowNode & { role: Role; children?: CashflowNode[]; members?: CashflowNode[]; parent?: Node;
    x: number; y: number; h: number; out: number; in: number };
  type Link = { s: Node; t: Node; v: number; role: "in" | "out" | "neutral"; d: string };

  let width = $state(0);
  const nodeW = 12, pad = 8, topPad = 28, bottomPad = 10, minSlot = 16;   // every node gets at least one text line of room
  const left = 170, right = 190;

  const chart = $derived.by(() => {
    const total = Math.max(cf.total_in, cf.total_out);
    const W = Math.max(720, width || 720);
    const node = (n: CashflowNode & Partial<Node>, role: Role): Node => ({ ...n, role, x: 0, y: 0, h: 0, out: 0, in: 0 });
    // ---- nodes, by column
    const inNodes = cf.income.map((n) => node(n, "in"));
    if (cf.total_out > cf.total_in) inNodes.push(node({ name: "From your balance", value: +(cf.total_out - cf.total_in).toFixed(2) }, "neutral"));
    const hub = node({ name: monthName, value: total }, "hub");
    const small = cf.spending.filter((n) => n.value < cf.total_out * 0.02);
    const outNodes = cf.spending.filter((n) => n.value >= cf.total_out * 0.02).map((n) => node(n, "out"));
    if (small.length === 1) outNodes.push(node(small[0], "out"));
    else if (small.length) outNodes.push(node({ name: `Everything else (${small.length})`, value: +small.reduce((s, n) => s + n.value, 0).toFixed(2),
      children: [], members: small }, "out"));
    if (cf.total_in > cf.total_out) outNodes.push(node({ name: "Left over", value: +(cf.total_in - cf.total_out).toFixed(2) }, "neutral"));
    const subNodes: Node[] = [];
    for (const n of outNodes) for (const k of n.children || []) subNodes.push({ ...node(k, "out"), parent: n });
    const cols = subNodes.length ? [inNodes, [hub], outNodes, subNodes] : [inNodes, [hub], outNodes];

    // ---- geometry: 300px of band height for the whole month
    const k = 300 / total;
    const colX = cols.map((_, i) => left + (i * (W - left - right - nodeW)) / (cols.length - 1));
    const colHeight = (col: Node[]) => col.reduce((s, n) => s + Math.max(minSlot, n.value * k), 0) + pad * (col.length - 1);
    const H = Math.max(360, Math.max(...cols.map(colHeight)) + topPad + bottomPad);
    cols.forEach((col, ci) => {
      let yy = topPad + (H - topPad - bottomPad - colHeight(col)) / 2;
      for (const n of col) {
        const slot = Math.max(minSlot, n.value * k);
        n.x = colX[ci]; n.h = Math.max(1.5, n.value * k); n.y = yy + (slot - n.h) / 2;
        yy += slot + pad;
      }
    });
    // ---- links, each band stacked below the ones before it at both ends
    const links: Link[] = [];
    const band = (s: Node, t: Node, v: number, role: Link["role"]) => {
      const th = v * k, x0 = s.x + nodeW, x1 = t.x, xm = (x0 + x1) / 2, y0 = s.y + s.out, y1 = t.y + t.in;
      s.out += th; t.in += th;
      links.push({ s, t, v, role,
        d: `M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1} L${x1},${y1 + th} C${xm},${y1 + th} ${xm},${y0 + th} ${x0},${y0 + th} Z` });
    };
    for (const n of inNodes) band(n, hub, n.value, n.role === "in" ? "in" : "neutral");
    for (const n of outNodes) band(hub, n, n.value, n.role === "neutral" ? "neutral" : "out");
    for (const c of subNodes) band(c.parent!, c, c.value, "out");
    return { W, H, hub, inNodes, outNodes, subNodes, links, all: cols.flat() };
  });

  const FILL: Record<Role, string> = { in: "var(--flow-in)", out: "var(--flow-out)", hub: "color-mix(in oklab, var(--foreground) 70%, transparent)", neutral: "var(--muted-foreground)" };

  // The readout: a name, an amount, and its share of money in or of spending.
  let tip = $state<{ name: string; v: number; role: Role; members?: CashflowNode[] } | null>(null);
  let box = $state<HTMLDivElement | null>(null), at = $state({ x: 0, y: 0 });
  function show(e: MouseEvent, t: NonNullable<typeof tip>) {
    const r = box!.getBoundingClientRect();
    tip = t; at = { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  const share = (v: number, base: number) => (base > 0 ? `${Math.round((v / base) * 100)}%` : "");
</script>

<div class="overflow-x-auto">
  <div class="relative min-w-[720px]" bind:this={box} bind:clientWidth={width}>
    {#if Math.max(cf.total_in, cf.total_out) > 0}
      {@const c = chart}
      <svg viewBox={`0 0 ${c.W} ${c.H}`} width={c.W} height={c.H} class="block overflow-visible text-xs" role="img"
        aria-label={`Cash flow for ${monthName}: money in on the left, spending on the right`}>
        {#each c.links as l, i (i)}
          <path d={l.d} role="presentation" style:fill={FILL[l.role]}
            class={["transition-[fill-opacity] hover:[fill-opacity:0.55]", l.role === "neutral" ? "[fill-opacity:0.2]" : "[fill-opacity:0.28]"]}
            onmousemove={(e) => show(e, { name: `${l.s.role === "hub" ? "" : l.s.name + " → "}${l.t.role === "hub" ? monthName : l.t.name}`, v: l.v, role: l.role })}
            onmouseleave={() => (tip = null)} />
        {/each}
        {#each c.all as n, i (i)}
          <rect x={n.x} y={n.y} width={nodeW} height={n.h} rx="2" style:fill={FILL[n.role]} stroke="var(--card)" role="presentation"
            onmousemove={(e) => show(e, { name: n.name, v: n.value, role: n.role, members: n.members })} onmouseleave={() => (tip = null)} />
        {/each}
        <!-- Labels: money in on the left, the month above its bar, everything else to the right. -->
        {#snippet label(n: Node, x: number, anchor: "start" | "end")}
          <text {x} y={n.y + n.h / 2 + 4} text-anchor={anchor} class="pointer-events-none fill-foreground [paint-order:stroke] [stroke-linejoin:round]"
            stroke="var(--card)" stroke-width="4">{n.name} <tspan class="fill-muted-foreground">{fmt0(n.value)}</tspan></text>
        {/snippet}
        {#each c.inNodes as n, i (i)}{@render label(n, n.x - 8, "end")}{/each}
        <text x={c.hub.x + nodeW / 2} y={c.hub.y - 10} text-anchor="middle" font-weight="600"
          class="pointer-events-none fill-foreground [paint-order:stroke] [stroke-linejoin:round]" stroke="var(--card)" stroke-width="4">
          {monthName} <tspan class="fill-muted-foreground">{fmt0(cf.total_in)} in · {fmt0(cf.total_out)} out</tspan></text>
        {#each c.outNodes as n, i (i)}{@render label(n, n.x + nodeW + 8, "start")}{/each}
        {#each c.subNodes as n, i (i)}{@render label(n, n.x + nodeW + 8, "start")}{/each}
      </svg>
      {#if tip}
        <Tip x={at.x} y={at.y} boxWidth={width} place="above">
          <div class="text-muted-foreground">{tip.name}</div>
          <div class="text-[15px] font-semibold tabular-nums">{fmt(tip.v)}</div>
          {#if tip.role === "in" || tip.role === "out"}
            <div class="flex justify-between gap-3 text-muted-foreground">
              <span>{tip.role === "in" ? "of money in" : "of spending"}</span>
              <span class="tabular-nums">{share(tip.v, tip.role === "in" ? cf.total_in : cf.total_out)}</span>
            </div>
          {/if}
          {#if tip.members}
            <div class="text-muted-foreground">{#each tip.members as mm, j (j)}{j ? " · " : ""}<span class="whitespace-nowrap">{mm.name} {fmt0(mm.value)}</span>{/each}</div>
          {/if}
        </Tip>
      {/if}
    {/if}
  </div>
</div>
