<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState, reload } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import { toast } from "svelte-sonner";
  import { inputCls } from "./ui";

  // The SimpleFIN setup-token flow: paste a token, and Runway claims it and runs the first sync. Used by Connect a bank
  // and by "Replace the connection" on the SimpleFIN row; `ondone` runs once it's connected.
  let { ondone }: { ondone?: () => void } = $props();
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
      ondone?.();
    } catch (err) { toast.error((err as Error).message); busy = ""; }
  }
</script>

<div class="flex flex-col items-start gap-3">
  <textarea class={`${inputCls} h-auto min-h-20 w-full py-2 font-mono`} bind:value={token} aria-label="SimpleFIN setup token"
    placeholder="Paste a SimpleFIN setup token (or an access URL, if you already claimed one)" autocomplete="off" spellcheck="false"></textarea>
  <Button disabled={!!busy} onclick={connect}>{busy || "Connect and sync"}</Button>
</div>
