<script lang="ts" module>
  // The tab you were on last, so #setup (the sidebar link) opens where you left off, like the classic app.
  let lastSection = "";
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import { app, reload } from "$lib/app.svelte";
  import { loadCategories } from "$lib/categories.svelte";
  import SubTabs from "$lib/components/SubTabs.svelte";
  import AccountsSection from "$lib/components/settings/AccountsSection.svelte";
  import BackupSection from "$lib/components/settings/BackupSection.svelte";
  import CategoriesSection from "$lib/components/settings/CategoriesSection.svelte";
  import ConnectionsSection from "$lib/components/settings/ConnectionsSection.svelte";
  import NotificationsSection from "$lib/components/settings/NotificationsSection.svelte";
  import RulesSection from "$lib/components/settings/RulesSection.svelte";
  import type { Rule, SettingsAccount } from "$lib/components/settings/types";
  import { linkCls } from "$lib/components/settings/ui";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";

  let { sub = "" }: { page?: string; sub?: string } = $props();

  const SECTIONS = [
    { id: "accounts", label: "Accounts" }, { id: "categories", label: "Categories" }, { id: "rules", label: "Rules" },
    { id: "connections", label: "Connections" }, { id: "notifications", label: "Notifications" }, { id: "backup", label: "Backup" },
  ];
  // svelte-ignore state_referenced_locally
  lastSection = sub || lastSection;
  // Until you've connected a bank, Settings opens on Connections.
  const section = SECTIONS.some((s) => s.id === lastSection) ? lastSection : app.state?.connected ? "accounts" : "connections";

  // Every tab has the accounts, rules and categories to hand, as in the classic page.
  const data = Promise.all([loadCategories(), api<SettingsAccount[]>("/api/accounts"), api<Rule[]>("/api/rules")])
    .then(([, accounts, rules]) => ({ accounts, rules }));
  let ruleCount = $state(0);
  data.then((d) => (ruleCount = d.rules.length), () => {});

  const version = $derived(app.state?.version && app.state.version !== "dev" ? app.state.version : "");
</script>

<h1 class="mb-4 text-[34px] leading-tight font-bold tracking-tight">Settings</h1>
<SubTabs label="Settings" current={section}
  tabs={SECTIONS.map((s) => ({ ...s, href: `#setup/${s.id}`, badge: s.id === "rules" ? ruleCount : undefined }))} />

{#await data}
  <div class="h-40 animate-pulse rounded-xl bg-muted"></div>
{:then d}
  <div class="flex flex-col gap-6">
    {#if section === "accounts"}<AccountsSection accounts={d.accounts} />
    {:else if section === "categories"}<CategoriesSection />
    {:else if section === "rules"}<RulesSection rules={d.rules} accounts={d.accounts} />
    {:else if section === "connections"}<ConnectionsSection accounts={d.accounts} />
    {:else if section === "notifications"}<NotificationsSection />
    {:else}<BackupSection />{/if}
  </div>
{:catch err}
  <Card.Root>
    <Card.Content>
      <p class="text-sm">Something went wrong: {err.message}</p>
      <Button class="mt-3" variant="outline" onclick={reload}>Try again</Button>
    </Card.Content>
  </Card.Root>
{/await}

<p class="mt-8 text-center text-sm text-muted-foreground">
  Runway {version || "development build"}{#if version}{" · "}<a class={linkCls} target="_blank" rel="noopener"
    href={`https://github.com/AnthonyPluth/Runway/releases/tag/${encodeURIComponent(version)}`}>what's new</a>{/if}
</p>
