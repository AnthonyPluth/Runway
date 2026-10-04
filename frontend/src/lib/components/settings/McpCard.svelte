<script lang="ts">
  import { api } from "$lib/api";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { copyText } from "$lib/copy";
  import { Badge } from "$lib/components/ui/badge";
  import { Button } from "$lib/components/ui/button";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { relTime } from "$lib/format";
  import { toast } from "svelte-sonner";
  import { inputCls, warnText } from "./ui";

  // AI assistants connect to Runway's MCP endpoint (/mcp) by its address and sign in with OAuth: you approve each one
  // on Runway's consent page. This shows the address, holds the switches that let them change churning, categorize, or
  // change anything, at all (each with what it allows, in a line under it: that's what you're agreeing to), and lists the assistants
  // connected (and disconnects them).
  type Connection = { id: number; client: string | null; who: string | null; scope: string[]; created: string | null; last_used: string | null };
  type Status = { allow_writes: boolean; allow_categorize: boolean; allow_all: boolean; oauth: boolean; url: string | null; reason: string | null; connections: Connection[] };
  let status = $state<Status | null>(null);
  let error = $state("");
  let address = $state<HTMLInputElement | null>(null);

  async function load() {
    try { status = await api<Status>("/api/mcp-settings"); error = ""; } catch (err) { error = (err as Error).message; }
  }
  load();
  async function setWrites(e: Event) {
    const box = e.currentTarget as HTMLInputElement, allow = box.checked;
    try {
      const r = await api<{ allow_writes: boolean }>("/api/mcp-settings/writes", { method: "POST", body: { allow } });
      if (status) status = { ...status, allow_writes: r.allow_writes };
      toast.success(r.allow_writes ? "Assistants can change churning" : "Assistants can only read again");
    } catch (err) { box.checked = !allow; toast.error((err as Error).message); await load(); }   // as it was: nothing changed
  }
  async function setCategorize(e: Event) {
    const box = e.currentTarget as HTMLInputElement, allow = box.checked;
    try {
      const r = await api<{ allow_categorize: boolean }>("/api/mcp-settings/categorize", { method: "POST", body: { allow } });
      if (status) status = { ...status, allow_categorize: r.allow_categorize };
      toast.success(r.allow_categorize ? "Assistants can categorize" : "Assistants can't categorize any more");
    } catch (err) { box.checked = !allow; toast.error((err as Error).message); await load(); }   // as it was: nothing changed
  }
  async function setAll(e: Event) {
    const box = e.currentTarget as HTMLInputElement, allow = box.checked;
    try {
      const r = await api<{ allow_all: boolean }>("/api/mcp-settings/all", { method: "POST", body: { allow } });
      if (status) status = { ...status, allow_all: r.allow_all };
      toast.success(r.allow_all ? "Assistants can change anything, asking first" : "Assistants can't change everything any more");
    } catch (err) { box.checked = !allow; toast.error((err as Error).message); await load(); }   // as it was: nothing changed
  }
  function access(scope: string[]): string {
    if (scope.includes("write")) return "Read + any change";
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

  // The switches. `allows` is what turning it on lets an assistant do: consent text, so it's shown, in one line.
  const SWITCHES = [
    { id: "mcp-writes", label: "Let assistants change churning", on: () => !!status?.allow_writes, set: setWrites,
      allows: "Mark benefits used; add or update cards, benefits, to-dos and plans. Never deletes.",
      hint: "Applies to every assistant you allowed to change churning when you connected it. It can't touch accounts, transactions or settings. Turn it off any time and it stops at once." },
    { id: "mcp-categorize", label: "Let assistants categorize", on: () => !!status?.allow_categorize, set: setCategorize,
      allows: "Set or accept the category of a transaction or order item. No deleting, splitting or renaming.",
      hint: "Applies to every assistant you allowed to categorize when you connected it. If you ask it to, it can remember a category for the merchant or item; it can't add categories. Turn it off any time and it stops at once." },
    { id: "mcp-all", label: "Let assistants change anything", on: () => !!status?.allow_all, set: setAll,
      allows: "Add, change and remove your data, asking you first. Never bank connections, API keys, notifications or these settings.",
      hint: "Applies to every assistant you allowed to change anything when you connected it: transactions, budgets, categories, rules, recurring items, accounts, net worth, equity, churning and orders. Turn it off any time and it stops at once." },
  ];
</script>

<Group title="AI assistants (MCP)">
  {#if error}
    <p class="cell text-sm text-muted-foreground">{error}</p>
  {:else if !status}
    <p class="cell text-sm text-muted-foreground">Loading…</p>
  {:else}
    <div class="cell flex-col items-stretch gap-2">
      <span class="flex flex-wrap items-center gap-2">
        <input class={`${inputCls} w-full font-mono sm:w-96`} readonly value={url} aria-label="MCP address" title={addHelp} bind:this={address} />
        <Button variant="outline" size="sm" onclick={() => copyText(url, address)}>Copy</Button>
        <a class="text-sm text-primary" href="https://anthonypluth.github.io/Runway/using/mcp/" target="_blank" rel="noopener">How to connect</a>
      </span>
      {#if !status.oauth}<p class={`text-sm ${warnText}`}>{status.reason}</p>{/if}
    </div>
    {#each SWITCHES as s (s.id)}
      <label class="cell cursor-pointer items-start" title={s.hint}>
        <input type="checkbox" class="mt-0.5 size-4 shrink-0 cursor-pointer accent-primary" checked={s.on()} onchange={s.set}
          aria-label={s.label} aria-describedby={`${s.id}-allows`} />
        <span class="flex min-w-0 flex-col gap-0.5">
          <span class="text-sm font-medium">{s.label}</span>
          <span id={`${s.id}-allows`} class="text-xs text-muted-foreground">{s.allows}</span>
        </span>
      </label>
    {/each}
  {/if}
</Group>

{#if status?.connections.length}
  <Group title="Connected assistants">
    {#each status.connections as c (c.id)}
      <div class="cell flex-wrap gap-y-1">
        <span class="flex min-w-0 flex-1 flex-col text-sm">
          <span class="flex flex-wrap items-center gap-1.5">{c.client || "Unnamed app"}
            <Badge variant="secondary">{access(c.scope)}</Badge></span>
          <span class="text-xs text-muted-foreground">Approved {relTime(c.created)}{c.who ? ` by ${c.who}` : ""} ·
            {c.last_used ? `last used ${relTime(c.last_used)}` : "not used yet"}</span>
        </span>
        <ConfirmButton confirm="Disconnect?" class="text-destructive" onconfirm={() => revoke(c)}>Disconnect</ConfirmButton>
      </div>
    {/each}
  </Group>
{/if}
