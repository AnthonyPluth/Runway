<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import { showTransactions, type ShowFilters } from "$lib/filters.svelte";
  import { fmt0, pct } from "$lib/format";
  import { catFill, catFilter, drill } from "./look";
  import Tip from "./Tip.svelte";
  import type { Cashflow, CashflowNode } from "./types";

  // The month as a Sankey: money in on the left flows into the month, and out to spending on the right (then to
  // subcategories). Small spending (under 2%) is gathered into "Everything else". Spending wears its categories'
  // colors, as in Breakdown. Hover a band or a bar for its amount and share; click it for its transactions. On a touch
  // screen the first tap selects it (its line shows under the chart) and a second opens it; from the keyboard, focus
  // selects and Enter opens. From 560px up it's laid out at least 620px wide and scaled down to fit. Narrower (a phone)
  // it's laid out at its own width: no subcategory column, names shortened with amounts under them; the table has
  // the rest.
  let { cf, monthName }: { cf: Cashflow; monthName: string } = $props();

  type Role = "in" | "out" | "hub" | "neutral";
  type Node = CashflowNode & { role: Role; children?: CashflowNode[]; members?: CashflowNode[]; parent?: Node; other?: boolean;
    color: string; x: number; y: number; h: number; out: number; in: number; key: string };
  type Link = { s: Node; t: Node; v: number; role: "in" | "out" | "neutral"; d: string; color: string; key: string };

  let width = $state(0);
  // Every node gets at least one text line of room (two when narrow: a name, and its amount under it).
  const nodeW = 12, pad = 8, topPad = 28, bottomPad = 10;
  const NARROW = 560, LAYOUT = 620;
  const narrow = $derived(width > 0 && width < NARROW);
  const short = (s: string, n = 16) => (s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s);

  const chart = $derived.by(() => {
    const total = Math.max(cf.total_in, cf.total_out);
    const W = narrow ? width : Math.max(LAYOUT, width);
    const left = narrow ? 76 : 170, right = narrow ? Math.min(110, W * 0.36) : 190, minSlot = narrow ? 30 : 16;
    const node = (n: CashflowNode & Partial<Node>, role: Role, color: string, col: number): Node =>
      ({ ...n, role, color, x: 0, y: 0, h: 0, out: 0, in: 0, key: `${col}:${n.name}` });
    // ---- nodes, by column
    const inNodes = cf.income.map((n) => node(n, "in", "var(--flow-in)", 0));
    if (cf.total_out > cf.total_in) inNodes.push(node({ name: "From your balance", value: +(cf.total_out - cf.total_in).toFixed(2) }, "neutral", "var(--muted-foreground)", 0));
    const hub = node({ name: monthName, value: total }, "hub", "color-mix(in oklab, var(--foreground) 70%, transparent)", 1);
    const small = cf.spending.filter((n) => n.value < cf.total_out * 0.02);
    const outNodes = cf.spending.filter((n) => n.value >= cf.total_out * 0.02).map((n) => node(n, "out", catFill(n.name), 2));
    if (small.length === 1) outNodes.push(node(small[0], "out", catFill(small[0].name), 2));
    else if (small.length) outNodes.push(node({ name: `Everything else (${small.length})`, value: +small.reduce((s, n) => s + n.value, 0).toFixed(2),
      children: [], members: small, other: true }, "out", catFill("", true), 2));
    if (cf.total_in > cf.total_out) outNodes.push(node({ name: "Left over", value: +(cf.total_in - cf.total_out).toFixed(2) }, "neutral", "var(--muted-foreground)", 2));
    const subNodes: Node[] = [];
    if (!narrow) for (const n of outNodes) for (const k of n.children || []) subNodes.push({ ...node(k, "out", n.color, 3), parent: n, key: `3:${n.name}>${k.name}` });
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
    const band = (s: Node, t: Node, v: number, role: Link["role"], color: string) => {
      const th = v * k, x0 = s.x + nodeW, x1 = t.x, xm = (x0 + x1) / 2, y0 = s.y + s.out, y1 = t.y + t.in;
      s.out += th; t.in += th;
      links.push({ s, t, v, role, color, key: `${s.key}>${t.key}`,
        d: `M${x0},${y0} C${xm},${y0} ${xm},${y1} ${x1},${y1} L${x1},${y1 + th} C${xm},${y1 + th} ${xm},${y0 + th} ${x0},${y0 + th} Z` });
    };
    for (const n of inNodes) band(n, hub, n.value, n.role === "in" ? "in" : "neutral", n.color);
    for (const n of outNodes) band(hub, n, n.value, n.role === "neutral" ? "neutral" : "out", n.color);
    for (const c of subNodes) band(c.parent!, c, c.value, "out", c.color);
    return { W, H, hub, inNodes, outNodes, subNodes, links, all: cols.flat() };
  });

  // What's behind a node: its category that month (a subcategory's "general" part is its category), all of the
  // month for the month itself, the month's spending for "Everything else", and nothing for the balance's lines.
  function filtersOf(n: Node): ShowFilters | null {
    const month = cf.month;
    if (n.role === "neutral") return null;
    if (n.role === "hub") return drill({ month });
    if (n.other) return drill({ month, kind: "out" });
    if (n.parent && n.name.endsWith(" (general)")) return drill({ month, category: catFilter(n.parent.name) });
    return drill({ month, category: catFilter(n.name) });
  }
  // A band: what's behind the end of it further from the month (its income, its category, its subcategory).
  const linkFilters = (l: Link) => filtersOf(l.t.role === "hub" ? l.s : l.t);
  const linkName = (l: Link) => `${l.s.name} → ${l.t.name}`;

  // The readout (hover) and the selection (a tap or focus, kept under the chart): a name, an amount, its share.
  type Sel = { key: string; name: string; v: number; role: Role; members?: CashflowNode[]; filters: ShowFilters | null };
  const ofNode = (n: Node): Sel => ({ key: n.key, name: n.name, v: n.value, role: n.role, members: n.members, filters: filtersOf(n) });
  const ofLink = (l: Link): Sel => ({ key: l.key, name: linkName(l), v: l.v, role: l.role, filters: linkFilters(l) });
  let tip = $state<Sel | null>(null), sel = $state<Sel | null>(null);
  let box = $state<HTMLDivElement | null>(null), at = $state({ x: 0, y: 0 });
  function show(e: MouseEvent, t: Sel) {
    const r = box!.getBoundingClientRect();
    tip = t; at = { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  let touch = false, pointing = false;
  const down = (e: PointerEvent) => { touch = e.pointerType !== "mouse"; pointing = true; };
  function activate(t: Sel) {
    pointing = false;
    if ((touch && sel?.key !== t.key) || !t.filters) { sel = t; tip = null; return; }
    showTransactions(t.filters);
  }
  // Focus from the keyboard selects; a tap or a click focuses too, but it's handled as one (activate).
  function focused(t: Sel) { if (!pointing) sel = t; }
  function key(e: KeyboardEvent, t: Sel) {
    pointing = false;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (t.filters) showTransactions(t.filters); }
    if (e.key === "Escape") sel = null;
  }
  const share = (t: Sel) => {
    const base = t.role === "in" ? cf.total_in : t.role === "out" ? cf.total_out : 0;
    return base > 0 ? `${pct(t.v / base)} of ${t.role === "in" ? "money in" : "money out"}` : "";
  };
  const ariaOf = (t: Sel) => [`${t.name}: ${fmt0(t.v)}`, share(t)].filter(Boolean).join(", ");
  // The selection goes when the month changes or what it pointed at is gone.
  $effect(() => { if (sel && !chart.all.some((n) => n.key === sel!.key) && !chart.links.some((l) => l.key === sel!.key)) sel = null; });
</script>

<div bind:clientWidth={width}>
  <!-- Nothing until it's been measured: a chart laid out for no width would flash at the wrong size. -->
  {#if width > 0 && Math.max(cf.total_in, cf.total_out) > 0}
    {@const c = chart}
    <div class="relative" bind:this={box}>
      <svg viewBox={`0 0 ${c.W} ${c.H}`} width="100%" class="block h-auto overflow-visible text-[12.5px]" role="group"
        aria-label={`Cash flow for ${monthName}: money in on the left, spending on the right`}>
        {#each c.links as l (l.key)}
          {@const t = ofLink(l)}
          <path d={l.d} style:fill={l.color} role="button" tabindex="0" aria-label={ariaOf(t)} data-key={l.key}
            class={["outline-none transition-[fill-opacity] hover:[fill-opacity:0.55] focus-visible:[fill-opacity:0.55]", t.filters && "cursor-pointer",
              sel?.key === l.key ? "[fill-opacity:0.6]" : l.role === "neutral" ? "[fill-opacity:0.2]" : "[fill-opacity:0.3]"]}
            onmousemove={(e) => show(e, t)} onmouseleave={() => (tip = null)} onpointerdown={down} onclick={() => activate(t)}
            onfocus={() => focused(t)} onkeydown={(e) => key(e, t)}>
            {#if t.filters}<title>See transactions: {t.name}</title>{/if}
          </path>
        {/each}
        {#each c.all as n (n.key)}
          {@const t = ofNode(n)}
          <rect x={n.x} y={n.y} width={nodeW} height={n.h} rx="2" style:fill={n.color} role="button"
            tabindex="0" aria-label={ariaOf(t)} data-key={n.key}
            stroke={sel?.key === n.key ? "var(--foreground)" : "var(--card)"} stroke-width={sel?.key === n.key ? 2 : 1}
            class={["outline-none focus-visible:stroke-foreground", t.filters && "cursor-pointer"]}
            onmousemove={(e) => show(e, t)} onmouseleave={() => (tip = null)} onpointerdown={down} onclick={() => activate(t)}
            onfocus={() => focused(t)} onkeydown={(e) => key(e, t)}>
            {#if t.filters}<title>See transactions: {n.name}</title>{/if}
          </rect>
        {/each}
        <!-- Labels: money in on the left, the month above its bar, everything else to the right. -->
        {#snippet label(n: Node, x: number, anchor: "start" | "end", name = n.name)}
          <text {x} y={n.y + n.h / 2 + 4} text-anchor={anchor} class="pointer-events-none fill-foreground [paint-order:stroke] [stroke-linejoin:round]"
            stroke="var(--card)" stroke-width="4">{name} <tspan class="fill-muted-foreground">{fmt0(n.value)}</tspan></text>
        {/snippet}
        {#if narrow}
          {#snippet two(n: Node, x: number, anchor: "start" | "end", name: string)}
            <text {x} y={n.y + n.h / 2 - 2} text-anchor={anchor} class="pointer-events-none fill-foreground [paint-order:stroke] [stroke-linejoin:round]"
              stroke="var(--card)" stroke-width="4">{name}<tspan {x} dy="15" class="fill-muted-foreground">{fmt0(n.value)}</tspan></text>
          {/snippet}
          {#each c.inNodes as n (n.key)}{@render two(n, n.x - 6, "end", short(n.name, 10))}{/each}
          {#each c.outNodes as n (n.key)}{@render two(n, n.x + nodeW + 6, "start", short(n.name, 14))}{/each}
          <text x={c.hub.x + nodeW / 2} y={c.hub.y - 10} text-anchor="middle" font-weight="600"
            class="pointer-events-none fill-foreground [paint-order:stroke] [stroke-linejoin:round]" stroke="var(--card)" stroke-width="4">{monthName}</text>
        {:else}
          {#each c.inNodes as n (n.key)}{@render label(n, n.x - 8, "end")}{/each}
          <text x={c.hub.x + nodeW / 2} y={c.hub.y - 10} text-anchor="middle" font-weight="600"
            class="pointer-events-none fill-foreground [paint-order:stroke] [stroke-linejoin:round]" stroke="var(--card)" stroke-width="4">
            {monthName} <tspan class="fill-muted-foreground">{fmt0(cf.total_in)} in · {fmt0(cf.total_out)} out</tspan></text>
          {#each c.outNodes as n (n.key)}{@render label(n, n.x + nodeW + 8, "start")}{/each}
          {#each c.subNodes as n (n.key)}{@render label(n, n.x + nodeW + 8, "start")}{/each}
        {/if}
      </svg>
      {#if tip}
        <Tip x={at.x} y={at.y} boxWidth={width} place="above">
          <div class="text-muted-foreground">{tip.name}</div>
          <div class="text-[15px] font-semibold tabular-nums">{fmt0(tip.v)}</div>
          {#if share(tip)}<div class="text-muted-foreground tabular-nums">{share(tip)}</div>{/if}
          {#if tip.members}
            <div class="text-muted-foreground">{#each tip.members as mm, j (j)}{j ? " · " : ""}<span class="whitespace-nowrap">{mm.name} {fmt0(mm.value)}</span>{/each}</div>
          {/if}
        </Tip>
      {/if}
    </div>
    <div class="mt-1 flex min-h-6 items-center gap-2 text-sm" aria-live="polite" data-selection>
      {#if sel}
        <span class="min-w-0 truncate"><span class="font-medium">{sel.name}</span> · <span class="tabular-nums">{fmt0(sel.v)}</span>{#if share(sel)}<span class="text-muted-foreground">{` · ${share(sel)}`}</span>{/if}</span>
        {#if sel.filters}
          {@const f = sel.filters}
          <Button variant="link" size="sm" class="ml-auto h-auto shrink-0 p-0 phone:min-h-11" onclick={() => showTransactions(f)}>Transactions</Button>
        {/if}
      {/if}
    </div>
  {/if}
</div>
