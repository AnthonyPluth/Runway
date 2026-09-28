<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState, reload } from "$lib/app.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate } from "$lib/format";
  import { accountName } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import X from "@lucide/svelte/icons/x";
  import type { Rule, RulePreview, SettingsAccount } from "./types";
  import { checkCls, fieldCls, inputCls, rowCls, selectCls } from "./ui";

  // Add or edit a rule: conditions on the left, what it does on the right, and a live count of what it would match.
  let { rule, accounts, onclose }: { rule: Rule | null; accounts: SettingsAccount[]; onclose: () => void } = $props();

  // svelte-ignore state_referenced_locally
  const r: Rule = rule ?? { match: "", match_mode: "contains", category: "", review: 0 };
  const str = (v: unknown) => (v == null ? "" : String(v));
  let mode = $state(r.match_mode || "contains");
  let match = $state(r.match || "");
  let min = $state(str(r.amount_min));
  let max = $state(str(r.amount_max));
  let direction = $state(r.direction || "");
  let account = $state(r.account_id || "");
  let category = $state(r.category || "");
  let rename = $state(r.rename || "");
  let review = $state(!!r.review);
  let applyPast = $state(!r.id);
  let parts = $state((r.split ?? []).map((p) => ({ category: p.category, percent: str(p.percent) })));
  let saving = $state(false);

  const total = $derived(parts.reduce((n, p) => n + (parseFloat(p.percent) || 0), 0));
  const off = $derived(Math.abs(total - 100) > 0.01);

  function toggleSplit() {
    if (parts.length) parts = [];
    else parts = [{ category: category || "", percent: "50" }, { category: "", percent: "50" }];
  }
  function dropPart(i: number) { parts.splice(i, 1); if (parts.length === 1) parts = []; }
  function addPart() {
    const left = 100 - total;
    parts.push({ category: "", percent: left > 0 ? String(Math.round(left * 100) / 100) : "" });
  }

  const body = () => ({
    match, match_mode: mode, amount_min: min, amount_max: max, direction, account_id: account,
    category: parts.length ? "" : category, rename, review,
    split: parts.length ? parts.map((p) => ({ category: p.category, percent: parseFloat(p.percent) })) : null,
  });

  // What it would match, a moment after you stop typing (only the latest answer counts).
  let preview = $state<RulePreview | null>(null);
  let seq = 0;
  $effect(() => {
    const b = JSON.stringify(body());
    const mine = ++seq;
    const timer = setTimeout(async () => {
      const p = await api<RulePreview>("/api/rules/preview", { method: "POST", body: JSON.parse(b) })
        .catch((e) => ({ error: (e as Error).message, matches: 0, changes: 0 }));
      if (mine === seq) preview = p;
    }, 250);
    return () => clearTimeout(timer);
  });

  async function save() {
    saving = true;
    try {
      let updated = 0;
      if (r.id) {
        await api(`/api/rules/${r.id}`, { method: "POST", body: body() });
        if (applyPast) updated = (await api<{ updated: number }>(`/api/rules/${r.id}/apply`, { method: "POST" })).updated;
      } else {
        updated = (await api<{ updated: number }>("/api/rules", { method: "POST", body: { ...body(), apply: applyPast } })).updated;
      }
      toast.success(`${r.id ? "Rule saved" : "Rule added"}${updated ? ` · ${updated} transaction${updated === 1 ? "" : "s"} updated` : ""}`);
      refreshState(); reload();
    } catch (err) { toast.error((err as Error).message); saving = false; }
  }
  const focus = (el: HTMLElement) => { el.focus(); };
  const legendCls = "mb-2 text-sm font-medium";
</script>

<div class="flex flex-col gap-4 rounded-lg border bg-muted/20 p-4" data-editor>
  <div class="grid gap-6 lg:grid-cols-2">
    <fieldset class="flex min-w-0 flex-col gap-3">
      <legend class={legendCls}>When a transaction</legend>
      <div class={`${fieldCls}`}>
        <label for="re-match-{r.id ?? 'new'}">Merchant or description</label>
        <span class="flex gap-2">
          <select class={cn(selectCls, "w-36 shrink-0")} aria-label="How the text matches" bind:value={mode}>
            <option value="contains">contains</option><option value="exact">is exactly</option><option value="starts">starts with</option>
          </select>
          <input id="re-match-{r.id ?? 'new'}" class={`${inputCls} flex-1`} bind:value={match} placeholder="whole foods" spellcheck="false" use:focus />
        </span>
      </div>
      <div class={rowCls}>
        <label class={`${fieldCls} w-28`}>Amount from<input class={`${inputCls} text-right`} type="number" min="0" step="0.01" inputmode="decimal"
          value={min} oninput={(e) => (min = e.currentTarget.value)} placeholder="any" /></label>
        <label class={`${fieldCls} w-28`}>to<input class={`${inputCls} text-right`} type="number" min="0" step="0.01" inputmode="decimal"
          value={max} oninput={(e) => (max = e.currentTarget.value)} placeholder="any" /></label>
        <label class={`${fieldCls} w-36`}>Direction
          <select class={selectCls} bind:value={direction}>
            <option value="">Either</option><option value="out">Money out</option><option value="in">Money in</option>
          </select>
        </label>
      </div>
      <label class={`${fieldCls} sm:max-w-sm`}>Account
        <select class={selectCls} bind:value={account}>
          <option value="">Any account</option>
          {#each accounts as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
        </select>
      </label>
    </fieldset>

    <fieldset class="flex min-w-0 flex-col gap-3">
      <legend class={legendCls}>Then</legend>
      <div class={rowCls}>
        <label class={`${fieldCls} w-full sm:w-80`}>Category
          <CategorySelect bind:value={category} blank="Leave it (other rules, history or AI decide)" disabled={parts.length > 0} class="w-full" />
        </label>
        <Button variant="link" size="sm" class="px-0" onclick={toggleSplit}>{parts.length ? "Don't split" : "Split instead…"}</Button>
      </div>
      {#if parts.length}
        <div class="flex flex-col gap-2">
          {#each parts as p, i (i)}
            <div class="flex items-center gap-2">
              <CategorySelect bind:value={p.category} class="min-w-0 flex-1" />
              <input class={`${inputCls} w-24 text-right`} type="number" min="0" max="100" step="0.01" aria-label="Percent"
                value={p.percent} oninput={(e) => (p.percent = e.currentTarget.value)} />
              <span class="text-sm text-muted-foreground">%</span>
              <Button variant="ghost" size="icon" aria-label="Remove this part" onclick={() => dropPart(i)}><X /></Button>
            </div>
          {/each}
          <div class="flex items-center justify-between">
            <Button variant="link" size="sm" class="px-0" onclick={addPart}>+ Add a part</Button>
            <span class={cn("text-sm tabular-nums", off ? "text-(--low)" : "text-muted-foreground")}>
              {off ? `${Math.round(total * 100) / 100}% of 100%` : "adds up"}</span>
          </div>
        </div>
      {/if}
      <label class={`${fieldCls} sm:max-w-sm`}>Rename the merchant to<input class={inputCls} bind:value={rename} placeholder="keep as is" /></label>
      <label class={checkCls}><input type="checkbox" bind:checked={review} /> Put it in Review so I look at it</label>
    </fieldset>
  </div>

  <p class="text-sm text-muted-foreground" aria-live="polite">
    {#if preview}{preview.error ? preview.error
      : `Matches ${preview.matches} past transaction${preview.matches === 1 ? "" : "s"}${preview.matches ? ` · applying it would change ${preview.changes}` : ""}`}{/if}
  </p>
  {#if preview?.examples?.length}
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <tbody>
          {#each preview.examples as t, i (i)}
            <tr class="border-t first:border-t-0">
              <td class="py-1.5 pr-3 whitespace-nowrap text-muted-foreground">{fmtDate(t.posted)}</td>
              <td class="py-1.5 pr-3">{t.payee || t.description}</td>
              <td class="py-1.5 pr-3 text-muted-foreground max-sm:hidden">{t.account_name}</td>
              <td class="py-1.5 pr-3 text-right tabular-nums">{fmt(t.amount)}</td>
              <td class="py-1.5 text-muted-foreground">{t.category || "—"}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <div class="flex flex-wrap items-center justify-between gap-3">
    <label class={checkCls}><input type="checkbox" bind:checked={applyPast} /> Apply to past transactions</label>
    <span class="flex gap-2">
      <Button variant="outline" onclick={onclose}>Cancel</Button>
      <Button disabled={saving} onclick={save}>{r.id ? "Save rule" : "Add rule"}</Button>
    </span>
  </div>
</div>
