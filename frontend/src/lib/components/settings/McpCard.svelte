<script lang="ts">
  import { api } from "$lib/api";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { relTime } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { helpCls, inputCls, titleNote } from "./ui";

  // The MCP server (runway/mcp_server.py): lets an AI assistant read your accounts, budget, reports and churning, and only
  // read. This makes (or replaces, or removes) its key, which is shown once and never opens anything that changes data.
  let status = $state<{ token: boolean; token_created: string | null } | null>(null);
  let error = $state("");
  let shownKey = $state("");
  let keyInput = $state<HTMLInputElement | null>(null);

  async function load() {
    try { status = await api("/api/mcp-key"); error = ""; } catch (err) { error = (err as Error).message; }
  }
  load();
  async function newKey() {
    try {
      const { token } = await api<{ token: string }>("/api/mcp-key", { method: "POST" });
      await load();
      shownKey = token;
    } catch (err) { toast.error((err as Error).message); }
  }
  async function removeKey() {
    try { await api("/api/mcp-key/remove", { method: "POST" }); toast.success("Key removed"); shownKey = ""; await load(); }
    catch (err) { toast.error((err as Error).message); }
  }
  function copy() {
    navigator.clipboard?.writeText(shownKey).then(() => toast.success("Copied"), () => {});
    keyInput?.select();
  }
  const selectOnMount = (el: HTMLInputElement) => { el.select(); };
  const command = $derived(`RUNWAY_URL=${location.origin} RUNWAY_MCP_KEY=${shownKey || "rwm_…"} python -m runway.mcp_server`);
</script>

<Card.Root>
  <Card.Header><Card.Title>AI assistants (MCP)<span class={titleNote}>optional, read-only</span></Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-3">
    {#if error}
      <p class="text-sm text-muted-foreground">{error}</p>
    {:else if !status}
      <p class="text-sm text-muted-foreground">Loading…</p>
    {:else}
      <p class={helpCls}>Runway's MCP server lets an assistant like Claude read your accounts, transactions, budget, reports, net worth and
        credit-card benefits, to answer questions about them. It can only read: it can't change anything, and it never sees your bank
        connections, settings or backups. It runs on your computer and talks to Runway with this key.</p>
      <div class={helpCls}>
        {#if status.token}
          Key made {relTime(status.token_created)}.
          <ConfirmButton class="h-auto px-1" confirm="Replace the key? Assistants using the old one stop working" onconfirm={newKey}>Make a new key</ConfirmButton>
          <ConfirmButton class="h-auto px-1" confirm="Remove? Assistants stop working" onconfirm={removeKey}>Remove</ConfirmButton>
        {:else}
          <Button variant="outline" size="sm" onclick={newKey}>Make a key</Button>
        {/if}
      </div>
      {#if shownKey}
        <span class="flex flex-wrap items-center gap-2">
          <input class={`${inputCls} w-full font-mono sm:w-96`} readonly value={shownKey} aria-label="MCP key" bind:this={keyInput} use:selectOnMount />
          <Button variant="outline" size="sm" onclick={copy}>Copy</Button>
          <span class="text-xs text-muted-foreground">Shown once: copy it now.</span>
        </span>
      {/if}
      <p class={helpCls}>In a checkout of Runway, add it to your assistant (for Claude Code:
        <code class="rounded bg-muted px-1 text-foreground">claude mcp add runway -e RUNWAY_URL={location.origin} -e RUNWAY_MCP_KEY=… -- python -m runway.mcp_server</code>),
        or run it by hand:</p>
      <code class="block overflow-x-auto rounded bg-muted px-2 py-1.5 text-xs whitespace-pre">{command}</code>
    {/if}
  </Card.Content>
</Card.Root>
