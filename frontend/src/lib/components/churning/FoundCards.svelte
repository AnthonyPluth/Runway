<script lang="ts">
  import { api } from "$lib/api";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import { fmt0 } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { toast } from "svelte-sonner";
  import { fullDate } from "./churning";
  import FoldedLine from "./FoldedLine.svelte";
  import type { Churning, Found, FoundDraft } from "./types";

  // "Found on your accounts": credit card accounts that aren't churning cards yet, each pre-filled from what Runway
  // knows. Add opens the add-card form with it for review (nothing is saved until you save the form); Dismiss hides
  // it, and can be undone.
  let { found, d, onadd, onchanged }: {
    found: Found; d: Pick<Churning, "issuers">; onadd: (draft: FoundDraft) => void; onchanged: () => void | Promise<void>;
  } = $props();

  const issuer = (key: string) => d.issuers.find((i) => i.key === key)?.name ?? "Other";
  const path = (id: string, what: string) => `/api/churning/found/${encodeURIComponent(id)}/${what}`;
  let showDismissed = $state(false);
  async function post(url: string) {
    await api(url, { method: "POST" });
    await onchanged();
  }
  async function dismiss(x: FoundDraft) {
    try {
      await post(path(x.account_id, "dismiss"));
      undoable(`Dismissed ${x.product || x.account_name}`, () => post(path(x.account_id, "undismiss")));
    } catch (err) { toast.error((err as Error).message); }
  }
  async function bringBack(id: string) {
    try { await post(path(id, "undismiss")); }
    catch (err) { toast.error((err as Error).message); }
  }
</script>

{#if found.drafts.length || found.dismissed.length}
  <section class="mb-6" aria-labelledby="found-title" data-testid="found-cards">
    {#if found.drafts.length}
      <h3 id="found-title" class="text-sm font-medium">Found on your accounts</h3>
      <ul class="divide-y rounded-lg border">
        {#each found.drafts as x (x.account_id)}
          <li class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
            <div class="min-w-0 flex-1">
              <div class="font-medium">{x.product || x.account_name}{#if x.business}<Badge variant="outline" class="ml-2">Business</Badge>{/if}</div>
              <div class="text-xs text-muted-foreground">
                {issuer(x.issuer)}{x.owner ? ` · ${x.owner}` : ""}{x.annual_fee ? ` · ${fmt0(x.annual_fee)} annual fee` : ""}{x.opened_on ? ` · opened on or before ${fullDate(x.opened_on)}` : ""}
              </div>
            </div>
            <Button size="sm" onclick={() => onadd(x)} aria-label={`Add ${x.product || x.account_name}`}>Add</Button>
            <Button size="sm" variant="ghost" onclick={() => dismiss(x)} aria-label={`Dismiss ${x.product || x.account_name}`}>Dismiss</Button>
          </li>
        {/each}
      </ul>
    {/if}
    {#if found.dismissed.length}
      <FoldedLine class="mt-2 text-xs" count={found.dismissed.length} noun="dismissed" bind:open={showDismissed} />
      {#if showDismissed}
        <ul class="mt-1 space-y-1 text-xs text-muted-foreground">
          {#each found.dismissed as x (x.account_id)}
            <li class="flex items-center gap-2">{x.name}<Button variant="link" size="sm" class="h-auto px-0 text-xs" onclick={() => bringBack(x.account_id)} aria-label={`Bring back ${x.name}`}>Bring back</Button></li>
          {/each}
        </ul>
      {/if}
    {/if}
  </section>
{/if}
