<script lang="ts">
  import { commas } from "$lib/commas";
  import { api } from "$lib/api";
  import { refreshState, reload } from "$lib/app.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { fmt, fmtDate } from "$lib/format";
  import { accountName } from "$lib/types";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import X from "@lucide/svelte/icons/x";
  import RuleApplyDialog from "./RuleApplyDialog.svelte";
  import { RuleApplyAsk, applyRule, countChanges, nothingToChange, ruleActions } from "./ruleApply.svelte";
  import type { Rule, RulePreview, SettingsAccount } from "./types";
  import { checkCls, fieldCls, inputCls, rowCls, selectCls } from "./ui";
  import { errMsg } from "$lib/act";
  import { debounced, latestOnly } from "$lib/debounce";

  // Add or edit a rule: conditions on the left, what it does on the right, and a live count of what it would match.
  // Save waits until the rule makes sense (a condition, something to do, a split adding up to 100%), saying what's
  // missing once you've started on it. With "Apply to past transactions" ticked (off unless you tick it), saving asks
  // first, as the list's Apply does, when it would change any, and its toast can undo that.
  let { rule, accounts, onclose }: { rule: Rule | null; accounts: SettingsAccount[]; onclose: () => void } = $props();
  const uid = $props.id();

  // svelte-ignore state_referenced_locally
  const r: Rule = rule ?? { match: "", match_mode: "contains", category: "", review: 0 };
  const str = (v: unknown) => (v == null ? "" : String(v));
  let mode = $state(r.match_mode || "contains");
  let match = $state(r.match || "");
  let min = $state(str(r.amount_min));
  let max = $state(str(r.amount_max));
  let direction = $state<Rule["direction"]>(r.direction || "");
  let account = $state(r.account_id || "");
  let category = $state(r.category || "");
  let rename = $state(r.rename || "");
  let review = $state(!!r.review);
  let applyPast = $state(false);
  let parts = $state((r.split ?? []).map((p) => ({ category: p.category, percent: str(p.percent) })));
  let saving = $state(false);

  const total = $derived(parts.reduce((n, p) => n + (parseFloat(p.percent) || 0), 0));
  const off = $derived(Math.abs(total - 100) > 0.01);

  // What has to be fixed before it can be saved (the server checks the same).
  const errors = $derived.by(() => {
    const e: { conditions?: string; amount?: string; split?: string; action?: string } = {};
    const text = match.trim();
    if (!text && min === "" && max === "" && !direction && !account) e.conditions = "Add a condition: some text, an amount, a direction or an account.";
    else if (text && text.length < 2) e.conditions = "Use at least two letters of text.";
    if (min !== "" && max !== "" && parseFloat(min) > parseFloat(max)) e.amount = "The first amount is bigger than the second.";
    if (parts.length) {
      if (off) e.split = `The parts add up to ${Math.round(total * 100) / 100}%, not 100%.`;
      else if (parts.some((p) => !p.category)) e.split = "Give every part a category.";
      else if (parts.some((p) => !(parseFloat(p.percent) > 0))) e.split = "Give every part a percentage.";
    } else if (!category && !rename.trim() && !review) e.action = "Choose what the rule does: a category, a new name or Review.";
    return e;
  });
  const problem = $derived(Object.values(errors)[0] ?? "");
  // Messages wait until you've changed something, so a new rule doesn't open covered in them.
  let touched = $state(false);
  const show = (k: keyof typeof errors) => (touched ? errors[k] : undefined);
  const errId = (k: keyof typeof errors) => (show(k) ? `${uid}-${k}` : undefined);

  function toggleSplit() {
    touched = true;
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
  const latest = latestOnly();
  const ask = debounced(async (b: string) => {
    const current = latest.begin();
    const p = await api<RulePreview>("/api/rules/preview", { method: "POST", body: JSON.parse(b) })
      .catch((e) => ({ error: errMsg(e), matches: 0, changes: 0 }));
    if (current()) preview = p;
  }, 250);
  $effect(() => {
    const b = JSON.stringify(body());
    latest.cancel();   // typing again overtakes an answer that's still on its way
    ask.call(b);
    return ask.cancel;
  });

  // Save the rule (without running it over the past); its id, or null with the error shown.
  async function persist(): Promise<number | null> {
    try {
      if (r.id) { await api(`/api/rules/${r.id}`, { method: "POST", body: body() }); return r.id; }
      return (await api<{ id: number }>("/api/rules", { method: "POST", body: { ...body(), apply: false } })).id;
    } catch (err) { toast.error(errMsg(err)); return null; }
  }
  const said = $derived(r.id ? "Rule saved" : "Rule added");
  const done = () => { refreshState(); reload(); };
  const asking = new RuleApplyAsk();

  async function save() {
    if (problem) { touched = true; return; }
    saving = true;
    let nothing: RulePreview | null = null;
    if (applyPast) {
      const p = await countChanges(body());
      if (!p) { saving = false; return; }
      if (p.changes) {
        saving = false;
        asking.ask(p.changes, ruleActions(body()), async () => {
          const id = await persist();
          if (id == null) return false;
          await applyRule(id, `${said} · `);   // saved even if this fails (the error is shown)
          done();
          return true;
        });
        return;
      }
      nothing = p;
    }
    if ((await persist()) == null) { saving = false; return; }
    toast.success(said, nothing ? { description: nothingToChange(nothing) } : undefined);
    done();
  }
  const focus = (el: HTMLElement) => { el.focus(); };
  const legendCls = "mb-2 text-sm font-medium";
</script>

{#snippet note(k: keyof typeof errors)}
  {#if show(k)}<p id="{uid}-{k}" class="text-xs text-destructive">{show(k)}</p>{/if}
{/snippet}

<div class="flex flex-col gap-4 rounded-lg border bg-muted/20 p-4" data-editor oninput={() => (touched = true)} onchange={() => (touched = true)}>
  <div class="grid gap-6 lg:grid-cols-2">
    <fieldset class="flex min-w-0 flex-col gap-3">
      <legend class={legendCls}>When a transaction</legend>
      <div class={`${fieldCls}`}>
        <label for="re-match-{r.id ?? 'new'}">Merchant or description</label>
        <span class="flex gap-2">
          <select class={cn(selectCls, "w-36 shrink-0")} aria-label="How the text matches" bind:value={mode}>
            <option value="contains">contains</option><option value="exact">is exactly</option><option value="starts">starts with</option>
          </select>
          <input id="re-match-{r.id ?? 'new'}" class={`${inputCls} flex-1 aria-invalid:border-destructive`} bind:value={match} placeholder="whole foods" spellcheck="false" use:focus
            aria-invalid={show("conditions") ? "true" : undefined} aria-describedby={errId("conditions")} />
        </span>
      </div>
      <div class={rowCls}>
        <label class={`${fieldCls} w-28`}>Amount from<input class={`${inputCls} text-right`} type="number" min="0" step="0.01" inputmode="decimal"
          value={min} {@attach commas} oninput={(e) => (min = e.currentTarget.value)} placeholder="any" /></label>
        <label class={`${fieldCls} w-28`}>Amount up to<input class={`${inputCls} text-right aria-invalid:border-destructive`} type="number" min="0" step="0.01" inputmode="decimal"
          value={max} {@attach commas} oninput={(e) => (max = e.currentTarget.value)} placeholder="any"
          aria-invalid={show("amount") ? "true" : undefined} aria-describedby={errId("amount")} /></label>
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
      {@render note("conditions")}
      {@render note("amount")}
    </fieldset>

    <fieldset class="flex min-w-0 flex-col gap-3" aria-describedby={errId("action")}>
      <legend class={legendCls}>Then</legend>
      <div class={rowCls}>
        <label class={`${fieldCls} w-full sm:w-80`}>Category
          <CategorySelect bind:value={category} blank="Leave it (other rules, history or AI decide)" disabled={parts.length > 0} class="w-full" onchange={() => (touched = true)} />
        </label>
        <Button variant="link" size="sm" class="px-0" onclick={toggleSplit}>{parts.length ? "Don't split" : "Split instead…"}</Button>
      </div>
      {#if parts.length}
        <div class="flex flex-col gap-2">
          {#each parts as p, i (i)}
            <div class="flex items-center gap-2">
              <CategorySelect bind:value={p.category} label={`Part ${i + 1} category`} class="min-w-0 flex-1" onchange={() => (touched = true)} />
              <input class={`${inputCls} w-24 text-right aria-invalid:border-destructive`} type="number" min="0" max="100" step="0.01" aria-label={`Part ${i + 1} percent`}
                value={p.percent} oninput={(e) => (p.percent = e.currentTarget.value)}
                aria-invalid={off ? "true" : undefined} aria-describedby={errId("split")} />
              <span class="text-sm text-muted-foreground">%</span>
              <Button variant="ghost" size="icon" aria-label="Remove this part" onclick={() => dropPart(i)}><X /></Button>
            </div>
          {/each}
          <div class="flex items-center justify-between">
            <Button variant="link" size="sm" class="px-0" onclick={addPart}>+ Add a part</Button>
            {#if !off}<span class="text-sm text-muted-foreground">adds up</span>{/if}
          </div>
          {@render note("split")}
        </div>
      {/if}
      <label class={`${fieldCls} sm:max-w-sm`}>Rename the merchant to<input class={inputCls} bind:value={rename} placeholder="keep as is" /></label>
      <label class={checkCls}><input type="checkbox" bind:checked={review} /> Put it in Review so I look at it</label>
      {@render note("action")}
    </fieldset>
  </div>

  <p class="text-sm text-muted-foreground" aria-live="polite">
    {#if preview && !errors.conditions && !errors.amount}{preview.error ? preview.error
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
      <span title={problem || undefined}><Button disabled={saving || !!problem} onclick={save}>{r.id ? "Save rule" : "Add rule"}</Button></span>
    </span>
  </div>
</div>

<RuleApplyDialog q={asking} />
