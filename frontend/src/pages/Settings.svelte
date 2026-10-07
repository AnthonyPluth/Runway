<script lang="ts" module>
  // The tab you were on last, so #setup (the sidebar link) opens where you left off, like the classic app.
  let lastSection = "";
</script>

<script lang="ts">
  import { loadAccounts } from "$lib/accounts";
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import { loadCategories } from "$lib/categories.svelte";
  import { openFeedback } from "$lib/monitoring";
  import SubTabs from "$lib/components/SubTabs.svelte";
  import AccountsSection from "$lib/components/settings/AccountsSection.svelte";
  import AdvancedSection from "$lib/components/settings/AdvancedSection.svelte";
  import CategoriesSection from "$lib/components/settings/CategoriesSection.svelte";
  import ConnectionsSection from "$lib/components/settings/ConnectionsSection.svelte";
  import NotificationsSection from "$lib/components/settings/NotificationsSection.svelte";
  import RulesSection from "$lib/components/settings/RulesSection.svelte";
  import { SECTIONS, resolveSection } from "$lib/components/settings/sections";
  import type { Rule, SettingsAccount } from "$lib/components/settings/types";
  import { linkCls } from "$lib/components/settings/ui";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";

  let { sub = "" }: { page?: string; sub?: string } = $props();

  // svelte-ignore state_referenced_locally
  lastSection = sub || lastSection;
  // Until you've connected a bank, Settings opens on Connections.
  const section = $derived(resolveSection(lastSection, !!app.state?.connected));

  // Each tab waits only for what it shows, so one request that fails (the rules, say) doesn't take the other tabs with
  // it: Accounts the accounts, Rules the rules with the accounts and categories, Categories the categories. Connections
  // shows straight away (its SimpleFIN row fills in its accounts when they come), as do Notifications and Advanced,
  // which load their own. Each request is made once per visit, however many tabs use it.
  const once = <T,>(load: () => Promise<T>) => { let p: Promise<T> | null = null; return () => (p ??= load()); };
  const accountsReq = once(() => loadAccounts());
  const rulesReq = once(() => api<Rule[]>("/api/rules"));
  const categoriesReq = once(() => loadCategories());
  type Data = { accounts: SettingsAccount[]; rules: Rule[] };
  const LOADS: Record<string, () => Promise<Partial<Data>>> = {
    accounts: () => accountsReq().then((accounts) => ({ accounts })),
    rules: () => Promise.all([rulesReq(), accountsReq(), categoriesReq()]).then(([rules, accounts]) => ({ rules, accounts })),
    categories: () => categoriesReq().then(() => ({})),
  };
  const data = $derived(LOADS[section]?.() ?? null);

  // The Rules tab's count, and the accounts the Connections tab shows, without holding anything up.
  let ruleCount = $state(0);
  rulesReq().then((r) => (ruleCount = r.length), () => {});
  let sideAccounts = $state<SettingsAccount[]>([]);
  $effect(() => { if (section === "connections") accountsReq().then((r) => (sideAccounts = r), () => {}); });

  const version = $derived(app.state?.version && app.state.version !== "dev" ? app.state.version : "");
</script>

<h1 class="mb-4 text-[34px] leading-[1.05] font-extrabold tracking-[-0.035em]">Settings</h1>
<SubTabs label="Settings" current={section}
  tabs={SECTIONS.map((s) => ({ ...s, href: `#setup/${s.id}`, badge: s.id === "rules" ? ruleCount : undefined }))} />

{#if !data}
  <div class="flex flex-col gap-6">
    {#if section === "connections"}<ConnectionsSection accounts={sideAccounts} />
    {:else if section === "notifications"}<NotificationsSection />
    {:else}<AdvancedSection />{/if}
  </div>
{:else}
  {#await data}
    <!-- Shaped like what's coming: a grouped list of rows. -->
    <div role="status" aria-busy="true">
      <span class="sr-only">Loading…</span>
      <div class="group-list" aria-hidden="true" style:--inset="3.75rem">
        {#each [0, 1, 2, 3, 4] as i (i)}
          <div class="cell">
            <div class="size-8 shrink-0 animate-pulse rounded-lg bg-muted motion-reduce:animate-none"></div>
            <div class="flex min-w-0 flex-1 flex-col gap-1.5">
              <div class="h-3.5 animate-pulse rounded bg-muted motion-reduce:animate-none" style:width={`${[44, 36, 52, 30, 40][i]}%`}></div>
              <div class="h-3 w-1/4 animate-pulse rounded bg-muted/60 motion-reduce:animate-none"></div>
            </div>
            <div class="h-3.5 w-16 shrink-0 animate-pulse rounded bg-muted motion-reduce:animate-none"></div>
          </div>
        {/each}
      </div>
    </div>
  {:then d}
    <div class="flex flex-col gap-6">
      {#if section === "accounts"}<AccountsSection accounts={d.accounts ?? []} />
      {:else if section === "categories"}<CategoriesSection />
      {:else if section === "rules"}<RulesSection rules={d.rules ?? []} accounts={d.accounts ?? []} />{/if}
    </div>
  {:catch err}
    <Card.Root>
      <Card.Content>
        <p class="text-sm">This tab didn’t load. The others still work.</p>
        {#if err?.message}<p class="mt-1 text-xs text-muted-foreground">{err.message}</p>{/if}
        <Button class="mt-3" variant="outline" onclick={reload}>Try again</Button>
      </Card.Content>
    </Card.Root>
  {/await}
{/if}

{#if section === "data" && (version || app.state?.sentry)}
<p class="mt-8 text-center text-sm text-muted-foreground">
  {#if version}<a class={linkCls} target="_blank" rel="noopener"
    href={`https://github.com/AnthonyPluth/Runway/releases/tag/${encodeURIComponent(version)}`}>What's new in {version}</a>{/if}
  {#if version && app.state?.sentry}{" · "}{/if}
  {#if app.state?.sentry}<button type="button" class={linkCls} onclick={() => openFeedback()}>Send feedback</button>{/if}
</p>
{/if}
