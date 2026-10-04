<script lang="ts">
  import { api } from "$lib/api";
  import { refreshState } from "$lib/app.svelte";
  import CategorySelect from "$lib/components/CategorySelect.svelte";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { NativeSelect } from "$lib/components/ui/native-select";
  import * as Sheet from "$lib/components/ui/sheet";
  import { Segmented } from "$lib/components/ui/toggle-group";
  import { isoDay } from "$lib/format";
  import { undoable } from "$lib/undo";
  import { accountName, type Account } from "$lib/types";
  import { errMsg } from "$lib/act";

  // Add a transaction by hand (cash, a cheque the bank hasn't shown yet): it counts like a synced one. Undo (from the
  // toast) deletes it again. `account`: the one the list is filtered to, picked to start with.
  let { accounts, account = "", onchanged, onclose }: { accounts: Account[]; account?: string; onchanged: () => void; onclose: () => void } = $props();

  const choices = $derived(accounts.filter((a) => a.kind !== "investment"));
  // svelte-ignore state_referenced_locally
  let v = $state({ account: account || (accounts.find((a) => a.kind === "checking") ?? accounts.find((a) => a.kind !== "investment"))?.id || "",
    posted: isoDay(), payee: "", amount: "", way: "out", category: "", notes: "" });
  let error = $state("");
  let busy = $state(false);

  async function add(e: SubmitEvent) {
    e.preventDefault();
    const typed = String(v.amount ?? "").trim();   // a number field's value can come back as a number
    const n = Number(typed.replace(/[$,]/g, ""));
    error = !v.account ? "Choose an account" : !/^\d{4}-\d{2}-\d{2}$/.test(v.posted) ? "Enter a date"
      : !v.payee.trim() ? "Give it a name" : typed === "" || !isFinite(n) ? "Enter the amount" : "";
    if (error) return;
    busy = true;
    const amount = Math.round(Math.abs(n) * 100) / 100 * (v.way === "in" ? 1 : -1);
    try {
      const r = await api<{ id: string }>("/api/transactions", { method: "POST",
        body: { account: v.account, posted: v.posted, payee: v.payee.trim(), amount, category: v.category || null, notes: v.notes } });
      undoable(`Added ${v.payee.trim()}`, async () => {
        await api(`/api/transactions/${encodeURIComponent(r.id)}`, { method: "DELETE" });
        refreshState(); onchanged();
      });
      refreshState(); onchanged(); onclose();
    } catch (err) { error = errMsg(err); }
    busy = false;
  }
  const lbl = "flex flex-col gap-1.5 text-xs text-muted-foreground";
</script>

<Sheet.Header>
  <Sheet.Title>Add a transaction</Sheet.Title>
</Sheet.Header>

<form class="flex flex-col gap-4 px-4 pb-6" onsubmit={add} novalidate>
  <label class={lbl}>Account
    <NativeSelect bind:value={v.account} class="h-10 w-full text-foreground" required>
      <option value="" disabled>Choose…</option>
      {#each choices as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
    </NativeSelect>
  </label>
  <div class="grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] gap-3">
    <label class={lbl}>Date<Input type="date" bind:value={v.posted} class="text-foreground" required /></label>
    <label class={lbl}>Name<Input bind:value={v.payee} placeholder="Farmers market" class="text-foreground" required /></label>
  </div>
  <div class={lbl}>
    <label for="add-amount">Amount</label>
    <div class="flex items-center gap-2">
      <Input id="add-amount" type="number" inputmode="decimal" step="0.01" min="0" bind:value={v.amount} placeholder="0.00"
        class="min-w-0 flex-1 text-right text-foreground tabular-nums" required />
      <Segmented label="Money in or out" bind:value={v.way} options={[{ value: "out", label: "Money out" }, { value: "in", label: "Money in" }]} />
    </div>
  </div>
  <label class={lbl}>Category<CategorySelect bind:value={v.category} label="Category" class="h-10 w-full text-foreground" /></label>
  <label class={lbl}>Note
    <textarea bind:value={v.notes} rows="2" maxlength="1000"
      class="min-h-16 w-full rounded-lg border border-transparent px-3 py-2 text-base text-foreground outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input md:text-sm"></textarea>
  </label>
  {#if error}<p class="text-sm text-destructive" role="alert">{error}</p>{/if}
  <div class="flex justify-end gap-2">
    <Button variant="outline" onclick={onclose}>Cancel</Button>
    <Button type="submit" disabled={busy}>{busy ? "Adding…" : "Add"}</Button>
  </div>
</form>
