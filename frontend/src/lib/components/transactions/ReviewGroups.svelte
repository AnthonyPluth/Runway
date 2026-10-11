<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState } from "$lib/app.svelte";
  import CategoryChip, { chipButton } from "$lib/components/CategoryChip.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { fmtSigned, plural } from "$lib/format";
  import { undoBatched } from "$lib/undoBatch";
  import { ruleOffer } from "./remember.svelte";
  import { restoreTx, type Was } from "./restore";
  import type { RuleOffer, Tx } from "./types";
  import { act } from "$lib/act";

  // Review a merchant at a time: one row per merchant (how many, what they add up to) with one picker that sets all of
  // them. Each change can be undone from its toast, which also offers to use the category for the merchant from now on.
  // `onapplied`: these transactions have their category now (they leave the list).
  let { items, onapplied, onchanged }: { items: Tx[]; onapplied: (ids: string[]) => void; onchanged: () => void } = $props();

  const merchantOf = (t: Tx) => (t.payee || t.description || "").trim();
  const groups = $derived.by(() => {
    const by = new Map<string, { key: string; name: string; txs: Tx[]; sum: number }>();
    for (const t of items) {
      const name = merchantOf(t), key = name.toLowerCase();
      const g = by.get(key) ?? by.set(key, { key, name, txs: [], sum: 0 }).get(key)!;
      g.txs.push(t); g.sum += t.amount;
    }
    return [...by.values()].sort((a, b) => b.txs.length - a.txs.length || a.name.localeCompare(b.name));
  });
  // The category the whole group has, if it's one (a rule's or the AI's, say); otherwise the picker asks.
  const shared = (txs: Tx[]) => (txs.every((t) => t.category && t.category === txs[0].category) ? txs[0].category! : "");

  let busy = $state<Record<string, boolean>>({});
  async function apply(g: { key: string; name: string; txs: Tx[] }, category: string) {
    if (!category) return;
    const ids = g.txs.map((t) => t.id);
    await act(async () => {
      const r = await api<{ updated: number; was: Was[]; offer_rule?: RuleOffer | null }>("/api/transactions/bulk", { method: "POST", body: { ids, category } });
      const offer = r.offer_rule ? ruleOffer(ids[0], category, r.offer_rule, onchanged) : null;
      undoBatched(`${g.name} → ${category}`, async () => { await restoreTx(r.was); onchanged(); },
        { description: offer?.description || plural(r.updated, "transaction"), also: offer?.also });
      refreshState();
      onapplied(ids);
    }, { busy: (on) => (busy[g.key] = on) });
  }
</script>

<div role="list" aria-label="Merchants to review" class="group-list">
  {#each groups as g (g.key)}
    <div role="listitem" class="cell flex-wrap gap-y-1.5">
      <div class="min-w-0 flex-1 basis-48">
        <div class="truncate font-medium" title={g.name}>{g.name || "No name"}</div>
        <div class="text-xs text-muted-foreground tabular-nums">{plural(g.txs.length, "transaction")} · {fmtSigned(g.sum)}</div>
      </div>
      <CategorySelect value={shared(g.txs)} repick blank="Choose…" label={`Category for ${plural(g.txs.length, "transaction")} from ${g.name}`}
        disabled={busy[g.key]} class={chipButton("row", busy[g.key] && "opacity-60")} onchange={(c) => apply(g, c)}>
        <CategoryChip category={shared(g.txs)} empty="Choose…" />
      </CategorySelect>
    </div>
  {/each}
</div>
