<script lang="ts">
  import { app, refreshState, reload } from "$lib/app.svelte";
  import ConfirmButton from "$lib/components/ConfirmButton.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { toast } from "svelte-sonner";
  import { fieldCls, helpCls, inputCls } from "./ui";

  // Settings → Backup: download everything, or replace everything with a backup file.
  let file = $state<File | null>(null);
  let restoring = $state(false);

  async function restore() {
    if (!file) return;
    restoring = true;
    try {
      // The backup goes up as it is (not JSON), so this is a plain fetch rather than api().
      const res = await fetch("/api/restore", { method: "POST", headers: { "X-Runway": "1", "Content-Type": "application/octet-stream" }, body: file });
      const r = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(r.error || `Restore failed (${res.status})`);
      toast.success(`Restored backup from ${new Date(r.created).toLocaleString()} · ${r.transactions} transactions, ${r.accounts} accounts`);
      await refreshState(); reload();
    } catch (err) { toast.error((err as Error).message); restoring = false; }
  }
</script>

<Card.Root>
  <Card.Header><Card.Title>Backup &amp; restore</Card.Title></Card.Header>
  <Card.Content class="flex flex-col gap-4">
    <p class={helpCls}>Everything, including bank access and API keys: keep it private. Database: {app.state?.database === "postgres" ? "Postgres" : "SQLite"}.</p>
    <div><Button href="/api/backup" download>Download a backup</Button></div>
    <div class="flex flex-wrap items-end gap-3">
      <label class={`${fieldCls} w-full sm:w-80`}>Restore from a backup
        <input class={`${inputCls} cursor-pointer file:mr-3 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground`} type="file"
          accept=".gz,.json,application/gzip,application/json" onchange={(e) => (file = e.currentTarget.files?.[0] ?? null)} /></label>
      <ConfirmButton variant="outline" size="default" confirm="Replace everything here with this backup?" disabled={!file || restoring}
        onconfirm={restore}>{restoring ? "Restoring…" : "Restore…"}</ConfirmButton>
    </div>
  </Card.Content>
</Card.Root>
