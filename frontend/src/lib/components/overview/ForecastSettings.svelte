<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState, reload } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import * as Sheet from "$lib/components/ui/sheet";
  import { fieldCls, helpCls, inputCls, selectCls } from "$lib/components/settings/ui";
  import { accountName, type Account } from "$lib/types";
  import Settings2 from "@lucide/svelte/icons/settings-2";
  import { toast } from "svelte-sonner";

  // Overview's forecast settings: the account the forecast shows and how far it looks. The account's name above the
  // balance is the trigger, so switching account is one click away. Saved like Settings used to (POST /api/settings).
  let { label, onhorizon }: { label: string; onhorizon?: (days: number) => void } = $props();

  let open = $state(false);
  let cash = $state<Account[]>([]);
  const st = $derived(app.state!);
  // With a single checking account and no choice made yet, that's the one the forecast shows.
  const onlyChecking = $derived(cash.filter((a) => a.kind === "checking").length === 1 ? cash.find((a) => a.kind === "checking")!.id : "");

  async function opened(o: boolean) {
    if (!o) return;
    try {
      const all = await api<Account[]>("/api/accounts");
      cash = all.filter((a) => !a.hidden && (a.kind === "checking" || a.kind === "savings"));
    } catch (err) { toast.error((err as Error).message); }
  }
  async function setPrimary(e: Event) {
    try {
      await api("/api/settings", { method: "POST", body: { primary_account: (e.currentTarget as HTMLSelectElement).value } });
      toast.success("Primary account saved"); await refreshState(); reload();
    } catch (err) { toast.error((err as Error).message); }
  }
  async function setHorizon(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    const days = Number(f.value);
    await api("/api/settings", { method: "POST", body: { horizon_days: days } });
    // Overview loads the new length in place, so the sheet stays open and the page isn't drawn afresh.
    await refreshState(); onhorizon?.(days);
  }
</script>

<Sheet.Root bind:open onOpenChange={opened}>
  <Sheet.Trigger aria-label="Forecast settings"
    class="-mx-1.5 inline-flex cursor-pointer items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[15px] text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
    {label}<Settings2 class="size-4" aria-hidden="true" />
  </Sheet.Trigger>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>Forecast settings</Sheet.Title>
      <Sheet.Description>Which account the forecast shows, and how far ahead it looks.</Sheet.Description>
    </Sheet.Header>
    <div class="flex flex-col gap-4 px-4 pb-4">
      <label class={fieldCls}>Primary account
        <select class={selectCls} value={st.primary_account || onlyChecking} onchange={setPrimary}>
          {#if cash.length > 1 || !st.primary_account}<option value="">Choose…</option>{/if}
          {#each cash as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
        </select>
      </label>
      <label class={fieldCls}>Default forecast length (days)
        <input class={inputCls} type="number" min="14" max="365" value={st.horizon_days} use:autosave={setHorizon} />
      </label>
      <p class={helpCls}>The 1M–6M buttons under the chart change the range for now; this is the length Overview opens with.</p>
    </div>
  </Sheet.Content>
</Sheet.Root>
