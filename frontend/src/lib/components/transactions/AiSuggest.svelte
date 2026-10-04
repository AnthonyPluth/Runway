<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState } from "$lib/app.svelte";
  import { loadCategories } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import * as Alert from "$lib/components/ui/alert";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { fmt, plural } from "$lib/format";
  import { undoBatched } from "$lib/undoBatch";
  import { cn } from "$lib/utils";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { onDestroy } from "svelte";
  import { ruleOffer } from "./remember.svelte";
  import { restoreTx, type Was } from "./restore";
  import type { AiGroup, RuleOffer } from "./types";
  import { errMsg } from "$lib/act";

  // The AI's suggestions for what's in Review, one line per merchant, the biggest first (SHOWN of them, then "Show N
  // more"). Nothing changes until you apply one; a suggested new category is created when you apply it. Applying to
  // CONFIRM_AT or more transactions asks first, and every apply can be undone from its toast. "Apply all High" applies
  // every line the AI is sure of. A merchant you skip isn't asked about again (this browser remembers it) unless you
  // ask including the skipped ones. The page's button calls run().
  let { status = $bindable("idle"), onasked, onchanged }: {
    status?: "idle" | "asking" | "asked"; onasked: (failed: boolean) => void; onchanged: () => void;
  } = $props();

  type Line = AiGroup & { key: number; choice: string; busy: boolean; gone: boolean };
  let lines = $state<Line[] | null>(null);
  let error = $state("");
  let seconds = $state(0);
  let tick: ReturnType<typeof setInterval> | undefined;
  onDestroy(() => clearInterval(tick));

  // Skipped merchants, kept in this browser.
  const SKIP_KEY = "runway.ai-skipped";
  function readSkipped(): string[] {
    try { const v = JSON.parse(localStorage.getItem(SKIP_KEY) ?? "[]"); return Array.isArray(v) ? v.filter((x) => typeof x === "string") : []; }
    catch { return []; }
  }
  let skipped = $state(readSkipped());
  function writeSkipped(list: string[]) {
    skipped = list;
    try { localStorage.setItem(SKIP_KEY, JSON.stringify(list)); } catch { /* not remembered past this visit */ }
  }

  /** Ask the AI about what's in Review; `withSkipped` brings back the merchants skipped before. */
  export async function run(withSkipped = false) {
    if (withSkipped) writeSkipped([]);
    status = "asking"; error = ""; lines = null; seconds = 0; showAll = false;
    const began = Date.now();
    clearInterval(tick);
    tick = setInterval(() => (seconds = Math.round((Date.now() - began) / 1000)), 1000);
    let groups: AiGroup[];
    try { groups = await api<AiGroup[]>("/api/ai/suggest", { method: "POST", body: { skip: skipped } }); }
    catch (err) { clearInterval(tick); error = errMsg(err); status = "idle"; onasked(true); return; }
    clearInterval(tick);
    onasked(false);
    status = "asked";
    lines = groups.map((g, key) => ({ ...g, key, choice: g.new_category ? "__new__" : g.category ?? "", busy: false, gone: false }));
  }

  // How sure the AI is, in a word (the percent is in the pill's tooltip).
  const level = (c: number) => (c >= 0.9 ? "High" : c >= 0.7 ? "Medium" : "Low");
  const suggested = (l: Line) => !!(l.category || l.new_category);

  // Applied or skipped lines go; the heading and the note keep describing what the AI answered.
  const done = (l: Line) => { l.gone = true; };
  function skip(l: Line) { done(l); if (!skipped.includes(l.merchant)) writeSkipped([...skipped, l.merchant]); }
  const CONFIRM_AT = 10;
  let asking = $state(false);
  let confirming = $state<Line | null>(null);
  const chosen = (l: Line) => l.choice === "__new__" ? l.new_category?.name ?? "" : l.choice;
  function apply(l: Line) {
    if (!l.choice) { toast.error("Choose a category first"); return; }
    if (l.count < CONFIRM_AT) { send(l); return; }
    confirming = l; asking = true;
  }
  // `quiet`: one of several applied together, so no "always for this merchant?" offer for each.
  async function send(l: Line, quiet = false): Promise<boolean> {
    l.busy = true;
    try {
      const body: Record<string, unknown> = { tx_ids: l.tx_ids, direction: l.direction };
      if (l.choice === "__new__") body.new_category = l.new_category; else body.category = l.choice;
      const r = await api<{ updated: number; category: string; created: boolean; offer_rule: RuleOffer | null; was: Was[] }>(
        "/api/ai/apply", { method: "POST", body });
      if (r.created) await loadCategories();
      done(l); refreshState(); onchanged();
      // Applying categorizes; whether this merchant should always be that category is the toast's other button.
      const offer = r.offer_rule && !quiet ? ruleOffer(l.tx_ids[0], r.category, r.offer_rule, onchanged) : null;
      undoBatched(`${l.merchant}: ${r.category}${r.created ? " (new category)" : ""} applied to ${r.updated}`,
        async () => { await restoreTx(r.was); onchanged(); }, offer ? { description: offer.description || undefined, also: offer.also } : {});
      return true;
    } catch (err) { toast.error(errMsg(err)); l.busy = false; return false; }
  }

  const answered = $derived(lines ? lines.filter(suggested).length : 0);
  const left = $derived(lines?.filter((l) => !l.gone) ?? []);
  const SHOWN = 5;
  let showAll = $state(false);
  const visible = $derived(showAll ? left : left.slice(0, SHOWN));

  // "Apply all High": every line it's sure of, as suggested (or as you've changed it).
  const high = $derived(left.filter((l) => suggested(l) && l.confidence >= 0.9 && l.choice && !l.busy));
  let askingAll = $state(false);
  async function applyHigh() {
    for (const l of [...high]) await send(l, true);
  }
  function startHigh() {
    if (high.reduce((n, l) => n + l.count, 0) >= CONFIRM_AT) askingAll = true; else applyHigh();
  }

</script>

{#if status === "asking"}
  <Card.Root class="mb-6"><Card.Content class="text-center text-sm text-muted-foreground" aria-live="polite">
    Asking the AI about each merchant in Review… <span class="tabular-nums">{seconds ? `${seconds}s` : ""}</span>
  </Card.Content></Card.Root>
{:else if error}
  <Alert.Root variant="destructive" class="mb-6"><TriangleAlert /><Alert.Description><p>{error}</p></Alert.Description></Alert.Root>
{:else if lines && !lines.length}
  <Card.Root class="mb-6"><Card.Content class="text-center text-sm text-muted-foreground">
    Nothing waiting for a category.
    {#if skipped.length}<Button variant="link" size="sm" class="h-auto p-0" onclick={() => run(true)}>Ask again, including {plural(skipped.length, "skipped merchant")}</Button>{/if}
  </Card.Content></Card.Root>
{:else if lines && left.length}
  <Card.Root class="mb-6">
    <Card.Header class="flex flex-wrap items-center justify-between gap-2">
      <Card.Title title={answered ? undefined : "The AI didn't suggest anything this time. Try again, or switch to a stronger model in Settings → Connections."}>AI suggestions · {answered && answered < lines.length ? `${answered} of ${plural(lines.length, "merchant")}` : plural(lines.length, "merchant")}</Card.Title>
      {#if high.length}
        <Button size="sm" variant="outline" onclick={startHigh} title="Apply every suggestion the AI is at least 90% sure of">Apply all High <span class="tabular-nums text-muted-foreground">{high.length}</span></Button>
      {/if}
    </Card.Header>
    <Card.Content class="flex flex-col">
      {#each visible as l (l.key)}
        <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-t py-3 first:border-t-0 first:pt-0">
          <div class="min-w-0 flex-1 basis-60">
            <div class="flex items-center gap-1.5 text-sm font-medium">{l.merchant}{#if l.direction === "in"}<Badge variant="secondary">Money in</Badge>{/if}</div>
            <div class="text-xs text-muted-foreground tabular-nums">{plural(l.count, "transaction")} · {fmt(l.total)}</div>
            {#each l.examples as x (x)}<div class="truncate text-xs text-muted-foreground" title={x}>{x}</div>{/each}
          </div>
          <div class="flex items-center gap-2">
            <CategorySelect bind:value={l.choice} class="w-52" label={`Category for ${l.merchant}`}
              extra={l.new_category ? [{ value: "__new__", label: `✦ New: ${l.new_category.name}${l.new_category.parent ? ` (in ${l.new_category.parent})` : ""}` }] : undefined} />
            {#if suggested(l)}
              {@const lv = level(l.confidence)}
              <Badge class={cn(lv === "High" ? "bg-good/15 text-good" : lv === "Medium" ? "bg-warning/15 text-warning" : "bg-muted text-muted-foreground")}
                title={`${l.new_category ? "A new category · " : ""}${Math.round(l.confidence * 100)}% sure`}>{lv}</Badge>
            {:else}<Badge variant="secondary">No suggestion</Badge>{/if}
          </div>
          <div class="ml-auto flex gap-2">
            <Button size="sm" disabled={l.busy} onclick={() => apply(l)}>Apply to {l.count}</Button>
            <Button size="sm" variant="outline" onclick={() => skip(l)} title="Don’t ask about this merchant again">Skip</Button>
          </div>
        </div>
      {/each}
      {#if left.length > SHOWN}
        <Button variant="link" size="sm" class="self-start px-0" aria-expanded={showAll} onclick={() => (showAll = !showAll)}>
          {showAll ? "Show fewer" : `Show ${left.length - SHOWN} more`}</Button>
      {/if}
    </Card.Content>
  </Card.Root>
{/if}

{#if confirming}
  <ConfirmDialog bind:open={asking} title={`Apply ${chosen(confirming)} to ${plural(confirming.count, "transaction")}?`}
    description={`${confirming.merchant} · ${fmt(confirming.total)}. ${confirming.choice === "__new__" ? "This adds the category, and they leave To review. " : "They leave To review. "}You can undo it afterwards.`}
    confirmLabel="Apply" busyLabel="Applying…" onconfirm={() => send(confirming!)} />
{/if}
{#if askingAll}
  <ConfirmDialog bind:open={askingAll} title={`Apply ${plural(high.length, "suggestion")} to ${plural(high.reduce((n, l) => n + l.count, 0), "transaction")}?`}
    description="Each merchant gets the category shown. They leave To review. You can undo it afterwards." confirmLabel="Apply" busyLabel="Applying…"
    onconfirm={applyHigh} />
{/if}
