<script lang="ts">
  import { app, boot, route, whenBooted } from "$lib/app.svelte";
  import MobileNav from "$lib/components/MobileNav.svelte";
  import Sidebar from "$lib/components/Sidebar.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Toaster } from "svelte-sonner";
  import type { Component } from "svelte";

  // Every page in pages/ is picked up here by name: pages/NetWorth.svelte is #networth. A page gets the route it
  // was opened at (`page`, e.g. "review" for Transactions) and the part after the slash (`sub`). Each page's code
  // loads the first time you open it, so the app starts quickly; once loaded it's kept.
  type Page = Component<{ page: string; sub: string }>;
  const modules = import.meta.glob<{ default: Page }>("./pages/*.svelte");
  const LOADERS: Record<string, () => Promise<{ default: Page }>> = {};
  for (const [file, load] of Object.entries(modules)) {
    LOADERS[file.slice("./pages/".length, -".svelte".length).toLowerCase()] = load;
  }
  // Routes that open another page: Review is a tab of Transactions, and #setup is Settings.
  const ALIASES: Record<string, string> = { review: "transactions", setup: "settings" };
  // Anything else (an old bookmark, a typo) opens Overview.
  const loaded: Record<string, Page> = $state({});
  let loadError = $state("");
  const nameFor = (p: string) => (LOADERS[ALIASES[p] ?? p] ? ALIASES[p] ?? p : "overview");
  const current = $derived(nameFor(route.page));
  $effect(() => {
    const name = current;
    if (loaded[name]) return;
    loadError = "";
    LOADERS[name]().then((m) => { loaded[name] = m.default; })
      .catch((err) => { console.error(err); loadError = "This page couldn't be loaded. Check your connection and try again."; });
  });
  // Start fetching the other pages once the first one is up, so moving around later is instant.
  whenBooted(() => setTimeout(() => Object.values(LOADERS).forEach((load) => load().catch(() => {})), 1500));
</script>

<!-- On a phone in the installed app the page runs under the status bar (viewport-fit=cover): a solid strip keeps what scrolls
     by from showing through behind the clock, and the page starts below it (main's top padding). -->
<div aria-hidden="true" class="fixed inset-x-0 top-0 z-40 h-[env(safe-area-inset-top)] bg-background md:hidden"></div>

<div class="flex min-h-dvh flex-col md:flex-row">
  <Sidebar />
  <main class="min-w-0 flex-1 p-4 pt-[calc(env(safe-area-inset-top)+1rem)] pb-[calc(env(safe-area-inset-bottom)+5.5rem)] md:p-8">
    <div class="mx-auto max-w-7xl">
      {#if app.bootError && !app.state}
        <Card.Root class="mx-auto mt-10 max-w-md">
          <Card.Header>
            <Card.Title>Can't reach Runway</Card.Title>
            <Card.Description>{app.bootError}</Card.Description>
          </Card.Header>
          <Card.Content><Button variant="outline" onclick={boot}>Try again</Button></Card.Content>
        </Card.Root>
      {:else if loadError && !loaded[current]}
        <Card.Root class="mx-auto mt-10 max-w-md">
          <Card.Header><Card.Title>Couldn't open this page</Card.Title><Card.Description>{loadError}</Card.Description></Card.Header>
          <Card.Content><Button variant="outline" onclick={() => location.reload()}>Try again</Button></Card.Content>
        </Card.Root>
      {:else if app.state && loaded[current]}
        <!-- Churning's tabs (cards, benefits, bank bonuses) are one page's views of the same data, so they aren't redrawn. -->
        {#key `${route.page}/${route.page === "churning" ? "" : route.sub}/${app.version}`}
          {@const Page = loaded[current]}
          <Page page={route.page} sub={route.sub} />
        {/key}
      {:else}
        <div class="space-y-4" aria-busy="true" aria-label="Loading">
          <div class="h-9 w-48 animate-pulse rounded-md bg-muted"></div>
          <div class="grid gap-4 md:grid-cols-3">{#each [0, 1, 2] as i (i)}<div class="h-28 animate-pulse rounded-xl bg-muted"></div>{/each}</div>
          <div class="h-72 animate-pulse rounded-xl bg-muted"></div>
        </div>
      {/if}
    </div>
  </main>
</div>
<MobileNav />
<Toaster theme="dark" position="bottom-center" mobileOffset={{ bottom: "calc(env(safe-area-inset-bottom) + 5rem)" }} />
