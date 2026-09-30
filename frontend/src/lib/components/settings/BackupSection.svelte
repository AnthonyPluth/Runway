<script lang="ts">
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { toast } from "svelte-sonner";
  import { fieldCls, helpCls, inputCls, warnText } from "./ui";

  // Settings → Advanced: download everything, or replace everything with a backup file. A chosen file is read first
  // (POST /api/backup/inspect) so you see what it holds before typing RESTORE; the server keeps a copy of what was here.
  type Counts = { accounts: number; transactions: number; recurring: number; budgets: number; total: number };
  type Inspected = { created?: string | null; source?: string | null; counts: Counts; current: Counts };

  let file = $state<File | null>(null);
  let inspected = $state<Inspected | null>(null);
  let problem = $state("");
  let asking = $state(false);

  // The backup goes up as it is (not JSON), so these are plain fetches rather than api().
  async function upload<T>(path: string, f: File, failed: string): Promise<T> {
    const res = await fetch(path, { method: "POST", headers: { "X-Runway": "1", "Content-Type": "application/octet-stream" }, body: f });
    const r = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(r.error || `${failed} (${res.status})`);
    return r as T;
  }

  async function choose(f: File | null) {
    file = f; inspected = null; problem = "";
    if (!f) return;
    try {
      const r = await upload<Inspected>("/api/backup/inspect", f, "Couldn’t read that backup");
      if (file === f) inspected = r;   // not a file chosen before this one
    } catch (err) { if (file === f) problem = (err as Error).message; }
  }

  const n = (k: number, word: string) => `${k.toLocaleString("en-US")} ${word}${k === 1 ? "" : "s"}`;
  const when = (t: string | null | undefined) => {
    const d = t ? new Date(t) : null;
    return d && !isNaN(d.getTime()) ? d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }) : "an unknown date";
  };
  const source = (s: string | null | undefined) => (s === "postgres" ? "Postgres" : s === "sqlite" ? "SQLite" : s || "unknown database");
  const summary = $derived.by(() => {
    if (!inspected) return "";
    const c = inspected.counts;
    return `Backup from ${when(inspected.created)} (${source(inspected.source)}): ${n(c.accounts, "account")}, ${n(c.transactions, "transaction")}, `
      + `${n(c.recurring, "recurring item")}, ${n(c.budgets, "budget")}`;
  });
  const hasData = $derived(!!inspected && ["accounts", "transactions", "recurring", "budgets"].some((k) => inspected!.current[k as keyof Counts] > 0));

  async function restore() {
    if (!file) return false;
    try {
      const r = await upload<{ created?: string | null; safety_copy?: string | null }>("/api/restore", file, "Restore failed");
      toast.success(r.safety_copy ? `Restored. A copy of what was here is at ${r.safety_copy}` : `Restored the backup from ${when(r.created)}`);
      await refreshState(); reload();
    } catch (err) { toast.error((err as Error).message); return false; }
  }
</script>

<Card.Root>
  <Card.Header><Card.Title>Backup &amp; restore</Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-4">
    <p class={helpCls}>Everything, including bank access and API keys: keep it private. Database: {app.state?.database === "postgres" ? "Postgres" : "SQLite"}.</p>
    <div><Button href="/api/backup" download>Download a backup</Button></div>
    <div class="flex flex-col gap-2">
      <div class="flex flex-wrap items-end gap-3">
        <label class={`${fieldCls} w-full sm:w-80`}>Restore from a backup
          <input class={`${inputCls} cursor-pointer file:mr-3 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground`} type="file"
            accept=".gz,.json,application/gzip,application/json" onchange={(e) => choose(e.currentTarget.files?.[0] ?? null)} /></label>
        <Button variant="outline" disabled={!inspected} onclick={() => (asking = true)}>Restore…</Button>
      </div>
      {#if summary}<p class={helpCls}>{summary}</p>
      {:else if problem}<p class={`text-sm ${warnText}`}>{problem}</p>
      {:else if file}<p class={helpCls}>Reading the backup…</p>{/if}
    </div>
  </Card.Content>
</Card.Root>

<ConfirmDialog bind:open={asking} title="Replace everything with this backup?" confirmLabel="Restore" busyLabel="Restoring…" destructive
  typeToConfirm="RESTORE" onconfirm={restore}>
  {#snippet description()}
    <p>{summary}.</p>
    <p>This replaces everything in this Runway (currently {n(inspected?.current.transactions ?? 0, "transaction")}) with the backup. It can’t be undone.</p>
    {#if hasData}<p>Runway first saves a copy of what’s here now in its data folder.</p>{/if}
  {/snippet}
</ConfirmDialog>
