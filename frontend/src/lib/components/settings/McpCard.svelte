<script lang="ts">
  import { api } from "$lib/api";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { relTime } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { checkCls, helpCls, inputCls, titleNote, warnText } from "./ui";

  // AI assistants connect to Runway's MCP endpoint (/mcp) by its address and sign in with OAuth: you approve each one
  // on Runway's consent page. This shows the address, lists the assistants connected (and disconnects them), and holds
  // the switch that lets them change churning at all.
  type Connection = { id: number; client: string | null; who: string | null; scope: string[]; created: string | null; last_used: string | null };
  type Status = { allow_writes: boolean; oauth: boolean; url: string | null; reason: string | null; connections: Connection[] };
  let status = $state<Status | null>(null);
  let error = $state("");

  async function load() {
    try { status = await api<Status>("/api/mcp-settings"); error = ""; } catch (err) { error = (err as Error).message; }
  }
  load();
  async function setWrites(e: Event) {
    const allow = (e.currentTarget as HTMLInputElement).checked;
    try {
      const r = await api<{ allow_writes: boolean }>("/api/mcp-settings/writes", { method: "POST", body: { allow } });
      if (status) status = { ...status, allow_writes: r.allow_writes };
      toast.success(r.allow_writes ? "Assistants can change churning" : "Assistants can only read again");
    } catch (err) { toast.error((err as Error).message); await load(); }
  }
  async function revoke(c: Connection) {
    try {
      await api(`/api/mcp-settings/connections/${c.id}/revoke`, { method: "POST" });
      toast.success(`${c.client || "The assistant"} is disconnected`);
    } catch (err) { toast.error((err as Error).message); }
    await load();
  }
  const url = $derived(status?.url || `${location.origin}/mcp`);
  function copy() {
    navigator.clipboard?.writeText(url).then(() => toast.success("Copied"), () => {});
  }
</script>

<Card.Root>
  <Card.Header><Card.Title>AI assistants (MCP)<span class={titleNote}>optional</span></Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-3">
    {#if error}
      <p class="text-sm text-muted-foreground">{error}</p>
    {:else if !status}
      <p class="text-sm text-muted-foreground">Loading…</p>
    {:else}
      <p class={helpCls}>Let an assistant like Claude read your accounts, transactions, budget, reports, net worth, orders and churning to
        answer questions about them. It never sees your bank connections, settings or backups. Add this address to the assistant, and
        approve it in Runway when it asks.</p>
      <span class="flex flex-wrap items-center gap-2">
        <input class={`${inputCls} w-full font-mono sm:w-96`} readonly value={url} aria-label="MCP address" />
        <Button variant="outline" size="sm" onclick={copy}>Copy</Button>
      </span>
      {#if !status.oauth}
        <p class={`text-sm ${warnText}`}>{status.reason}</p>
      {/if}
      <p class={helpCls}>Claude Code: <code class="rounded bg-muted px-1 text-foreground">claude mcp add --transport http runway {url}</code>.
        Claude on the web or desktop: Settings → Connectors → Add custom connector.</p>

      <div class="flex flex-col">
        <span class="text-sm font-medium">Connected assistants</span>
        {#each status.connections as c (c.id)}
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 border-b py-2 last:border-b-0">
            <span class="flex min-w-0 flex-1 flex-col text-sm">
              <span class="flex flex-wrap items-center gap-1.5">{c.client || "Unnamed app"}
                <Badge variant="secondary">{c.scope.includes("churning:write") ? "Read + churning" : "Read"}</Badge></span>
              <span class="text-xs text-muted-foreground">Approved {relTime(c.created)}{c.who ? ` by ${c.who}` : ""} ·
                {c.last_used ? `last used ${relTime(c.last_used)}` : "not used yet"}</span>
            </span>
            <ConfirmButton confirm="Disconnect it?" onconfirm={() => revoke(c)}>Revoke</ConfirmButton>
          </div>
        {:else}
          <p class={helpCls}>None yet.</p>
        {/each}
      </div>

      <label class={`${checkCls} w-full rounded-lg border p-3`}>
        <input type="checkbox" checked={status.allow_writes} onchange={setWrites} aria-label="Let assistants change churning" />
        <span><b>Let assistants change churning</b> <span class={titleNote}>off unless you turn it on</span>
          <span class={`${helpCls} block`}>Applies to every connection. An assistant you allowed to change churning when you connected it can
            then mark a benefit used, add or update cards, benefits, to-dos and planned items, and check off a plan. It can't delete anything or
            touch accounts, transactions or settings. Turn it off any time and it stops at once.</span></span>
      </label>
    {/if}
  </Card.Content>
</Card.Root>
