<script lang="ts" module>
  import { act, errMsg } from "$lib/act";
  // What the last restore said that's worth keeping (where the copy of what it replaced went, keys it couldn't read):
  // it stays under Restore until dismissed, through the page redrawing after the restore.
  type Restored = { when: string; copy: string | null; unreadable: number };
  let lastRestore: Restored | null = null;
</script>

<script lang="ts">
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { Alert, AlertDescription } from "$lib/components/ui/alert";
  import { Button } from "$lib/components/ui/button";
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import Group from "$lib/components/ui/group/Group.svelte";
  import { toast } from "svelte-sonner";
  import X from "@lucide/svelte/icons/x";
  import { fieldCls, helpCls, inputCls, warnText } from "./ui";

  // Settings → Data: download everything, or replace everything with a backup file. A chosen file is read first
  // (POST /api/backup/inspect) so you see what it holds before typing RESTORE; the server keeps a copy of what was here.
  type Counts = { accounts: number; transactions: number; recurring: number; budgets: number; total: number };
  type Inspected = { created?: string | null; source?: string | null; counts: Counts; current: Counts };

  let file = $state<File | null>(null);
  let inspected = $state<Inspected | null>(null);
  let problem = $state("");
  let asking = $state(false);
  let restored = $state<Restored | null>(lastRestore);
  $effect(() => { lastRestore = restored; });

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
    } catch (err) { if (file === f) problem = errMsg(err); }
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

  // "Last backup: Sep 28": when one was last downloaded from here (the year too when it isn't this one).
  const lastBackup = $derived.by(() => {
    const d = app.state?.last_backup ? new Date(app.state.last_backup) : null;
    if (!d || isNaN(d.getTime())) return null;
    const year = d.getFullYear() !== new Date().getFullYear();
    return { day: d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(year ? { year: "numeric" } : {}) }), full: when(app.state!.last_backup) };
  });
  // The browser saves the file itself, so there's no telling when it's done: look again once it likely is.
  function downloaded() { setTimeout(() => { refreshState().catch(() => {}); }, 3000); }

  async function restore() {
    const f = file;
    if (!f) return false;
    if (!(await act(async () => {
      const r = await upload<{ created?: string | null; safety_copy?: string | null; unreadable_secrets?: string[] }>("/api/restore", f, "Restore failed");
      // The backup's bank access and API keys are encrypted with the key of the Runway that made it: under another
      // key they can't be read, and each is entered again in Settings (the note under Restore says so).
      restored = { when: when(r.created), copy: r.safety_copy || null, unreadable: r.unreadable_secrets?.length ?? 0 };
      toast.success("Restored");
      await refreshState(); reload();
    }))) return false;
  }
</script>

<Group title="Backup & restore">
  <div class="cell flex-col items-start gap-1.5">
    <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
      <Button href="/api/backup" download onclick={downloaded}
        title={`Everything, from the ${app.state?.database === "postgres" ? "Postgres" : "SQLite"} database. Restoring it elsewhere needs this Runway’s secret key.`}>Download a backup</Button>
      {#if lastBackup}<span class="text-sm text-muted-foreground" title={lastBackup.full}>Last backup: {lastBackup.day}</span>{/if}
    </div>
    <p class="text-xs text-muted-foreground">Your bank keys are in it, encrypted. Keep the file private.</p>
  </div>
  <div class="cell flex-col items-stretch gap-2">
    <div class="flex flex-wrap items-end gap-3">
      <label class={`${fieldCls} w-full sm:w-80`}>Restore from a backup
        <input class={`${inputCls} cursor-pointer file:mr-3 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground`} type="file"
          accept=".gz,.json,application/gzip,application/json" onchange={(e) => choose(e.currentTarget.files?.[0] ?? null)} /></label>
      <Button variant="outline" disabled={!inspected} onclick={() => (asking = true)}>Restore…</Button>
    </div>
    {#if summary}<p class={helpCls}>{summary}</p>
    {:else if problem}<p class={`text-sm ${warnText}`}>{problem}</p>
    {:else if file}<p class={helpCls}>Reading the backup…</p>{/if}
    {#if restored}
      <Alert class="pr-11">
        <AlertDescription class="flex flex-col gap-1.5 text-sm leading-relaxed">
          <p class="font-medium text-foreground">Restored the backup from {restored.when}.</p>
          {#if restored.copy}<p>A copy of what was here before is at <code class="rounded bg-muted px-1 break-all">{restored.copy}</code>.</p>{/if}
          {#if restored.unreadable}
            <p class={warnText}>{restored.unreadable} saved {restored.unreadable === 1 ? "key or connection" : "keys and connections"} can’t be read with this
              Runway’s secret key. Set the key the backup was made with as <code class="rounded bg-muted px-1">RUNWAY_SECRET_KEY_OLD</code> and restart, or
              enter them again in Settings.</p>
          {/if}
        </AlertDescription>
        <Button variant="ghost" size="icon" class="absolute top-1.5 right-1.5" aria-label="Dismiss" onclick={() => (restored = null)}><X /></Button>
      </Alert>
    {/if}
  </div>
</Group>

<ConfirmDialog bind:open={asking} title="Replace everything with this backup?" confirmLabel="Restore" busyLabel="Restoring…" destructive
  typeToConfirm="RESTORE" onconfirm={restore}>
  {#snippet description()}
    <p>{summary}.</p>
    <p>This replaces everything in this Runway (currently {n(inspected?.current.transactions ?? 0, "transaction")}) with the backup. It can’t be undone.</p>
    {#if hasData}<p>Runway first saves a copy of what’s here now in its data folder.</p>{/if}
  {/snippet}
</ConfirmDialog>
