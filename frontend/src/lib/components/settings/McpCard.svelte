<script lang="ts">
  import { api } from "$lib/api";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { relTime } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { checkCls, inputCls, titleNote, warnText } from "./ui";

  // AI assistants connect to Runway's MCP endpoint (/mcp) by its address and sign in with OAuth: you approve each one
  // on Runway's consent page. This shows the address, lists the assistants connected (and disconnects them), and holds
  // the switches that let them change churning, or categorize, at all.
  type Connection = { id: number; client: string | null; who: string | null; scope: string[]; created: string | null; last_used: string | null };
  type Status = { allow_writes: boolean; allow_categorize: boolean; oauth: boolean; url: string | null; reason: string | null; connections: Connection[] };
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
  async function setCategorize(e: Event) {
    const allow = (e.currentTarget as HTMLInputElement).checked;
    try {
      const r = await api<{ allow_categorize: boolean }>("/api/mcp-settings/categorize", { method: "POST", body: { allow } });
      if (status) status = { ...status, allow_categorize: r.allow_categorize };
      toast.success(r.allow_categorize ? "Assistants can categorize" : "Assistants can't categorize any more");
    } catch (err) { toast.error((err as Error).message); await load(); }
  }
  function access(scope: string[]): string {
    const extra = [scope.includes("churning:write") && "churning", scope.includes("categorize:write") && "categorizing"].filter(Boolean);
    return ["Read", ...extra].join(" + ");
  }
  async function revoke(c: Connection) {
    try {
      await api(`/api/mcp-settings/connections/${c.id}/revoke`, { method: "POST" });
      toast.success(`${c.client || "The assistant"} is disconnected`);
    } catch (err) { toast.error((err as Error).message); }
    await load();
  }
  const url = $derived(status?.url || `${location.origin}/mcp`);
  const addHelp = $derived(`Add this address to your assistant and approve it here when it asks. Claude Code: claude mcp add --transport http runway ${url}. Claude on the web or desktop: Settings → Connectors → Add custom connector.`);
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
      <span class="flex flex-wrap items-center gap-2">
        <input class={`${inputCls} w-full font-mono sm:w-96`} readonly value={url} aria-label="MCP address" title={addHelp} />
        <Button variant="outline" size="sm" onclick={copy}>Copy</Button>
      </span>
      {#if !status.oauth}
        <p class={`text-sm ${warnText}`}>{status.reason}</p>
      {/if}

      <div class="flex flex-col">
        {#if status.connections.length}<span class="text-sm font-medium">Connected assistants</span>{/if}
        {#each status.connections as c (c.id)}
          <div class="flex flex-wrap items-center gap-x-3 gap-y-1 border-b py-2 last:border-b-0">
            <span class="flex min-w-0 flex-1 flex-col text-sm">
              <span class="flex flex-wrap items-center gap-1.5">{c.client || "Unnamed app"}
                <Badge variant="secondary">{access(c.scope)}</Badge></span>
              <span class="text-xs text-muted-foreground">Approved {relTime(c.created)}{c.who ? ` by ${c.who}` : ""} ·
                {c.last_used ? `last used ${relTime(c.last_used)}` : "not used yet"}</span>
            </span>
            <ConfirmButton confirm="Disconnect it?" onconfirm={() => revoke(c)}>Revoke</ConfirmButton>
          </div>
        {/each}
      </div>

      <label class={`${checkCls} w-full rounded-lg border p-3`} title="Applies to every connection. An assistant you allowed to change churning when you connected it can then mark a benefit used, add or update cards, benefits, to-dos and planned items, and check off a plan. It can't delete anything or touch accounts, transactions or settings. Turn it off any time and it stops at once.">
        <input type="checkbox" checked={status.allow_writes} onchange={setWrites} aria-label="Let assistants change churning" />
        <span><b>Let assistants change churning</b> <span class={titleNote}>off unless you turn it on</span></span>
      </label>
      <label class={`${checkCls} w-full rounded-lg border p-3`} title="Applies to every connection. An assistant you allowed to categorize when you connected it can then set the category of a transaction or an order item, accept the one Runway suggested, and (if you ask it to) remember it for the merchant or item. It can't delete, split or rename anything, or add categories. Turn it off any time and it stops at once.">
        <input type="checkbox" checked={status.allow_categorize} onchange={setCategorize} aria-label="Let assistants categorize" />
        <span><b>Let assistants categorize</b> <span class={titleNote}>off unless you turn it on</span></span>
      </label>
    {/if}
  </Card.Content>
</Card.Root>
