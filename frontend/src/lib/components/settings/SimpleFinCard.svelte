<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import type { SyncLog } from "$lib/types";
  import { toast } from "svelte-sonner";
  import { helpCls, inputCls, linkCls, titleNote } from "./ui";

  // The SimpleFIN bank connection: paste a setup token, and Runway claims it and runs the first sync.
  const st = $derived(app.state!);
  // /api/state's last_log also has when it ran (`at`, UTC), which lib/types.ts doesn't list.
  const log = $derived(st.last_log as (SyncLog & { at?: string }) | null | undefined);
  let token = $state("");
  let busy = $state("");

  async function connect() {
    busy = "Connecting…";
    try {
      await api("/api/connect", { method: "POST", body: { token } });
      busy = "Syncing (first sync pulls ~6 months)…";
      const r = await api<{ new: number }>("/api/sync", { method: "POST" });
      toast.success(`Connected · ${r.new} transactions imported`);
      await refreshState(); reload();
    } catch (err) { toast.error((err as Error).message); busy = ""; }
  }
</script>

{#snippet form()}
  <div class="mt-3 flex flex-col items-start gap-3">
    <textarea class={`${inputCls} h-auto min-h-20 w-full py-2 font-mono`} bind:value={token} aria-label="SimpleFIN setup token"
      placeholder="Paste a SimpleFIN setup token (or an access URL, if you already claimed one)" autocomplete="off" spellcheck="false"></textarea>
    <Button disabled={!!busy} onclick={connect}>{busy || "Connect and sync"}</Button>
  </div>
{/snippet}

<Card.Root>
  <Card.Header><Card.Title>Bank connection <span class={titleNote}>SimpleFIN</span></Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-2 text-sm">
    {#if st.simplefin}
      <p>Connected.</p>
      {#if log}<p class="text-muted-foreground">Last sync: {log.at} UTC — {log.message}</p>{/if}
      <details>
        <summary class="cursor-pointer text-muted-foreground">Replace the connection</summary>
        {@render form()}
      </details>
    {:else}
      <p class={helpCls}>Paste a setup token from <a class={linkCls} href="https://beta-bridge.simplefin.org" target="_blank" rel="noopener">SimpleFIN Bridge</a>.</p>
      {@render form()}
    {/if}
  </Card.Content>
</Card.Root>
