<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import * as Sheet from "$lib/components/ui/sheet";
  import { checkCls, fieldCls, helpCls, inputCls, selectCls } from "$lib/components/settings/ui";
  import { isPhone } from "$lib/phone.svelte";
  import { accountName, type Account, type Overview } from "$lib/types";
  import Settings2 from "@lucide/svelte/icons/settings-2";
  import { toast } from "svelte-sonner";
  import { perDay } from "./assumptions";
  import { forecastSheet } from "./forecastSheet.svelte";

  // Overview's forecast settings: the account the forecast shows, whether it takes out everyday spending, and how far
  // it looks. The account's name above the balance is the trigger, so switching account is one click away; anything
  // else can open it too (see forecastSheet.svelte.ts). Saved like Settings used to (POST /api/settings), then
  // Overview loads its figures again in place (`onchange`), so the sheet stays open.
  let { label, accounts = [], onhorizon, onchange }: {
    label: string; accounts?: Overview["accounts"]; onhorizon?: (days: number) => void; onchange?: () => void;
  } = $props();

  let cash = $state<Account[]>([]);
  const st = $derived(app.state!);
  // With a single checking account and no choice made yet, that's the one the forecast shows.
  const onlyChecking = $derived(cash.filter((a) => a.kind === "checking").length === 1 ? cash.find((a) => a.kind === "checking")!.id : "");

  // "about $42 a day", or "Checking about $42 a day, Savings none" when several accounts are combined.
  const lately = $derived(accounts.filter((a) => a.daily_spend_estimate != null)
    .map((a) => `${accounts.length > 1 ? `${a.name} ` : ""}${a.daily_spend_estimate ? perDay(a.daily_spend_estimate) : "none"}`));

  // The account list, each time the sheet opens (from its trigger or from elsewhere).
  $effect(() => {
    if (!forecastSheet.open) return;
    api<Account[]>("/api/accounts").then(
      (all) => (cash = all.filter((a) => !a.hidden && (a.kind === "checking" || a.kind === "savings"))),
      (err) => toast.error((err as Error).message));
  });

  async function setPrimary(e: Event) {
    try {
      await api("/api/settings", { method: "POST", body: { primary_account: (e.currentTarget as HTMLSelectElement).value } });
      toast.success("Primary account saved"); await refreshState(); onchange?.();
    } catch (err) { toast.error((err as Error).message); }
  }
  async function setHorizon(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    const days = Number(f.value);
    await api("/api/settings", { method: "POST", body: { horizon_days: days } });
    // Overview loads the new length in place, so the sheet stays open and the page isn't drawn afresh.
    await refreshState(); onhorizon?.(days);
  }
  // The same setting as the account's "Subtract average everyday spending" in Settings → Accounts.
  async function setSpend(id: string, f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    await api(`/api/accounts/${encodeURIComponent(id)}`, { method: "POST", body: { daily_spend: (f as HTMLInputElement).checked ? 1 : 0 } });
    onchange?.();
  }
</script>

{#if isPhone()}
  <span class="inline-flex items-center py-0.5 text-[15px] text-muted-foreground">{label}</span>
{:else}
<Sheet.Root bind:open={forecastSheet.open}>
  <Sheet.Trigger aria-label="Forecast settings"
    class="-mx-1.5 inline-flex cursor-pointer items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[15px] text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
    {label}<Settings2 class="size-4" aria-hidden="true" />
  </Sheet.Trigger>
  <Sheet.Content>
    <Sheet.Header>
      <Sheet.Title>Forecast settings</Sheet.Title>
    </Sheet.Header>
    <div class="flex flex-col gap-4 px-4 pb-4">
      <label class={fieldCls}>Primary account
        <select class={selectCls} value={st.primary_account || onlyChecking} onchange={setPrimary}>
          {#if cash.length > 1 || !st.primary_account}<option value="">Choose…</option>{/if}
          {#each cash as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
        </select>
      </label>
      {#if accounts.length}
        <div class="flex flex-col gap-2">
          {#each accounts as a (a.id)}
            <label class={checkCls}>
              <input type="checkbox" checked={!!a.daily_spend_on} use:autosave={(f) => setSpend(a.id, f)} />
              {accounts.length > 1 ? `${a.name}: subtract average everyday spending` : "Subtract average everyday spending"}
            </label>
          {/each}
          {#if lately.length}<p class={helpCls}>Over the last 90 days: {lately.join(", ")}.</p>{/if}
        </div>
      {/if}
      <label class={fieldCls}>Default forecast length (days)
        <input class={inputCls} type="number" min="14" max="365" value={st.horizon_days} use:autosave={setHorizon} />
      </label>
    </div>
  </Sheet.Content>
</Sheet.Root>
{/if}
