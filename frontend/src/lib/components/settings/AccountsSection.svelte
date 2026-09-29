<script lang="ts">
  import { api } from "$lib/api";
  import { app, refreshState } from "$lib/app.svelte";
  import { autosave } from "$lib/autosave";
  import * as Alert from "$lib/components/ui/alert";
  import * as Card from "$lib/components/ui/card";
  import { accountName } from "$lib/types";
  import { toast } from "svelte-sonner";
  import TriangleAlert from "@lucide/svelte/icons/triangle-alert";
  import AccountRow from "./AccountRow.svelte";
  import type { SettingsAccount } from "./types";
  import { fieldCls, inputCls, linkCls, rowCls, selectCls } from "./ui";

  // Settings → Accounts: which account the forecast shows and for how long, then every account grouped by type.
  let { accounts }: { accounts: SettingsAccount[] } = $props();

  const st = $derived(app.state!);
  const cash = $derived(accounts.filter((a) => a.kind === "checking" || a.kind === "savings"));
  // With a single checking account and no choice made yet, that's the one the forecast shows.
  const onlyChecking = $derived(cash.filter((a) => a.kind === "checking").length === 1 ? cash.find((a) => a.kind === "checking")!.id : "");
  const byName = $derived(Object.fromEntries(accounts.map((a) => [a.id, accountName(a)])));

  const KIND_GROUPS: [string, string[]][] = [["Cash", ["checking", "savings"]], ["Credit cards", ["credit"]], ["Loans", ["loan"]], ["Investments", ["investment"]]];
  const groups = $derived([
    ...KIND_GROUPS.map(([title, kinds]) => ({ title, list: accounts.filter((a) => !a.hidden && kinds.includes(a.kind)) })),
    { title: "Hidden", list: accounts.filter((a) => a.hidden) },
  ].filter((g) => g.list.length));

  async function setPrimary(e: Event) {
    try {
      await api("/api/settings", { method: "POST", body: { primary_account: (e.currentTarget as HTMLSelectElement).value } });
      toast.success("Primary account saved"); await refreshState();
    } catch (err) { toast.error((err as Error).message); }
  }
  async function setHorizon(f: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement) {
    await api("/api/settings", { method: "POST", body: { horizon_days: Number(f.value) } });
    await refreshState();
  }
  const undecided = $derived(st.plaid_undecided ?? 0);
</script>

<Card.Root>
  <Card.Header><Card.Title>Forecast</Card.Title></Card.Header>
  <Card.Content class={rowCls}>
    <label class={`${fieldCls} w-full sm:w-72`}>Primary account (the one the forecast shows)
      <select class={selectCls} value={st.primary_account || onlyChecking} onchange={setPrimary}>
        {#if cash.length > 1 || !st.primary_account}<option value="">Choose…</option>{/if}
        {#each cash as a (a.id)}<option value={a.id}>{accountName(a)}</option>{/each}
      </select>
    </label>
    <label class={`${fieldCls} w-40`}>Forecast length (days)
      <input class={inputCls} type="number" min="14" max="365" value={st.horizon_days} use:autosave={setHorizon} />
    </label>
  </Card.Content>
</Card.Root>

{#if undecided}
  <Alert.Root>
    <TriangleAlert />
    <Alert.Description><p>
      {undecided === 1 ? "An account" : `${undecided} accounts`} from Plaid {undecided === 1 ? "is" : "are"} waiting for you to say what
      {undecided === 1 ? "it is" : "they are"}, so {undecided === 1 ? "it isn't" : "they aren't"} listed here or counted in net worth yet.
      <a class={linkCls} href="#setup/connections">Connections</a>
    </p></Alert.Description>
  </Alert.Root>
{/if}

<Card.Root>
  <Card.Header><Card.Title>Accounts</Card.Title></Card.Header>
  <Card.Content>
    {#each groups as g (g.title)}
      <section class="mb-4 last:mb-0" aria-label={g.title}>
        <h3 class="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{g.title}</h3>
        {#each g.list as a (a.id)}<AccountRow {a} {cash} {byName} />{/each}
      </section>
    {:else}
      <p class="py-6 text-center text-sm text-muted-foreground">Accounts appear here after the first sync.</p>
    {/each}
  </Card.Content>
</Card.Root>
