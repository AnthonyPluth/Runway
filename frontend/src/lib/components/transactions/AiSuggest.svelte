<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState } from "$lib/app.svelte";
  import { loadCategories } from "$lib/categories.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import * as Alert from "$lib/components/ui/alert";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { fmt, plural } from "$lib/format";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import { onDestroy } from "svelte";
  import { askRemember } from "./remember.svelte";
  import type { AiGroup } from "./types";

  // The AI's suggestions for what's in Review, one line per merchant. Nothing changes until you apply one; a
  // suggested new category is created when you apply it. The page's button calls run().
  let { status = $bindable("idle"), onasked, onchanged }: {
    status?: "idle" | "asking" | "asked"; onasked: (failed: boolean) => void; onchanged: () => void;
  } = $props();

  type Line = AiGroup & { key: number; choice: string; busy: boolean; gone: boolean };
  let lines = $state<Line[] | null>(null);
  let error = $state("");
  let seconds = $state(0);
  let tick: ReturnType<typeof setInterval> | undefined;
  onDestroy(() => clearInterval(tick));

  export async function run() {
    status = "asking"; error = ""; lines = null; seconds = 0;
    const began = Date.now();
    clearInterval(tick);
    tick = setInterval(() => (seconds = Math.round((Date.now() - began) / 1000)), 1000);
    let groups: AiGroup[];
    try { groups = await api<AiGroup[]>("/api/ai/suggest", { method: "POST" }); }
    catch (err) { clearInterval(tick); error = (err as Error).message; status = "idle"; onasked(true); return; }
    clearInterval(tick);
    onasked(false);
    status = "asked";
    lines = groups.map((g, key) => ({ ...g, key, choice: g.new_category ? "__new__" : g.category ?? "", busy: false, gone: false }));
  }

  // Applied or skipped lines go; the heading and the note keep describing what the AI answered.
  const done = (l: Line) => { l.gone = true; };
  async function apply(l: Line) {
    if (!l.choice) { toast.error("Choose a category first"); return; }
    l.busy = true;
    try {
      const body: Record<string, unknown> = { tx_ids: l.tx_ids, direction: l.direction };
      if (l.choice === "__new__") body.new_category = l.new_category; else body.category = l.choice;
      const r = await api<{ updated: number; category: string; created: boolean; offer_rule: { merchant: string; replaces?: string | null } | null }>(
        "/api/ai/apply", { method: "POST", body });
      if (r.created) await loadCategories();
      toast.success(`${l.merchant}: ${r.category}${r.created ? " (new category)" : ""} applied to ${r.updated}`);
      done(l); refreshState(); onchanged();
      // Applying categorizes; whether this merchant should always be that category is a separate question.
      if (r.offer_rule) askRemember(l.tx_ids[0], r.category, r.offer_rule, onchanged);
    } catch (err) { toast.error((err as Error).message); l.busy = false; }
  }
  const answered = $derived(lines ? lines.filter((g) => g.category || g.new_category).length : 0);
  const left = $derived(lines?.filter((l) => !l.gone) ?? []);
</script>

{#if status === "asking"}
  <Card.Root class="mb-6"><Card.Content class="text-center text-sm text-muted-foreground" aria-live="polite">
    Asking the AI about each merchant in Review… <span class="tabular-nums">{seconds ? `${seconds}s` : ""}</span>
  </Card.Content></Card.Root>
{:else if error}
  <Alert.Root variant="destructive" class="mb-6"><TriangleAlert /><Alert.Description><p>{error}</p></Alert.Description></Alert.Root>
{:else if lines && !lines.length}
  <Card.Root class="mb-6"><Card.Content class="text-center text-sm text-muted-foreground">Nothing waiting for a category.</Card.Content></Card.Root>
{:else if lines && left.length}
  <Card.Root class="mb-6">
    <Card.Header>
      <Card.Title>AI suggestions · {plural(lines.length, "merchant")}</Card.Title>
      <Card.Description>
        {answered === lines.length ? "The AI suggested a category for every merchant."
          : answered ? `The AI suggested a category for ${answered} of ${lines.length}; pick the rest yourself.`
          : "The AI didn't suggest anything this time. Try again, or switch to a stronger model in Settings → Connections (for example anthropic/claude-haiku-4.5)."}
      </Card.Description>
      <Card.Action class="text-xs text-muted-foreground">Nothing changes until you apply</Card.Action>
    </Card.Header>
    <Card.Content class="flex flex-col">
      {#each left as l (l.key)}
        <div class="flex flex-wrap items-center gap-x-4 gap-y-2 border-t py-3 first:border-t-0 first:pt-0">
          <div class="min-w-0 flex-1 basis-60">
            <div class="flex items-center gap-1.5 text-sm font-medium">{l.merchant}{#if l.direction === "in"}<Badge variant="secondary">money in</Badge>{/if}</div>
            <div class="text-xs text-muted-foreground tabular-nums">{plural(l.count, "transaction")} · {fmt(l.total)}</div>
            {#each l.examples as x (x)}<div class="truncate text-xs text-muted-foreground" title={x}>{x}</div>{/each}
          </div>
          <div class="flex items-center gap-2">
            <CategorySelect bind:value={l.choice} class="w-52">
              {#snippet first()}
                {#if l.new_category}<option value="__new__">✦ New: {l.new_category.name}{l.new_category.parent ? ` (in ${l.new_category.parent})` : ""}</option>{/if}
              {/snippet}
            </CategorySelect>
            {#if l.new_category}
              <Badge class="bg-primary/15 text-primary" title="Nothing existing fit, so the AI suggests adding this category. Applying creates it.">new category · {Math.round(l.confidence * 100)}%</Badge>
            {:else if l.category}
              <Badge class="bg-primary/15 text-primary" title="AI confidence">{Math.round(l.confidence * 100)}%</Badge>
            {:else}<Badge variant="secondary">no suggestion</Badge>{/if}
          </div>
          <div class="ml-auto flex gap-2">
            <Button size="sm" disabled={l.busy} onclick={() => apply(l)}>Apply to {l.count}</Button>
            <Button size="sm" variant="outline" onclick={() => done(l)}>Skip</Button>
          </div>
        </div>
      {/each}
    </Card.Content>
  </Card.Root>
{/if}
