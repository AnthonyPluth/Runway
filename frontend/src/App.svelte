<script lang="ts">
  import { app, boot, route } from "$lib/app.svelte";
  import MobileNav from "$lib/components/MobileNav.svelte";
  import Sidebar from "$lib/components/Sidebar.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Toaster } from "svelte-sonner";
  import type { Component } from "svelte";

  // Every page in pages/ is picked up here by name: pages/NetWorth.svelte is #networth. A page gets the route it
  // was opened at (`page`, e.g. "review" for Transactions) and the part after the slash (`sub`).
  type Page = Component<{ page: string; sub: string }>;
  const modules = import.meta.glob<{ default: Page }>("./pages/*.svelte", { eager: true });
  const PAGES: Record<string, Page> = {};
  for (const [file, mod] of Object.entries(modules)) {
    const name = file.slice("./pages/".length, -".svelte".length).toLowerCase();
    PAGES[name] = mod.default;
  }
  // Routes that open another page: Review is a tab of Transactions, and #setup is Settings.
  const ALIASES: Record<string, string> = { review: "transactions", setup: "settings" };
  // Anything else (an old bookmark, a typo) opens Overview.
  const pageFor = (p: string) => PAGES[ALIASES[p] ?? p] ?? PAGES.overview;
</script>

<div class="flex min-h-dvh flex-col md:flex-row">
  <Sidebar />
  <main class="min-w-0 flex-1 p-4 pb-[calc(env(safe-area-inset-bottom)+5.5rem)] md:p-8">
    <div class="mx-auto max-w-7xl">
      {#if app.bootError && !app.state}
        <Card.Root class="mx-auto mt-10 max-w-md">
          <Card.Header>
            <Card.Title>Can't reach Runway</Card.Title>
            <Card.Description>{app.bootError}</Card.Description>
          </Card.Header>
          <Card.Content><Button variant="outline" onclick={boot}>Try again</Button></Card.Content>
        </Card.Root>
      {:else if app.state}
        {#key `${route.page}/${route.sub}/${app.version}`}
          {@const Page = pageFor(route.page)}
          <Page page={route.page} sub={route.sub} />
        {/key}
      {/if}
    </div>
  </main>
</div>
<MobileNav />
<Toaster theme="dark" position="bottom-center" mobileOffset={{ bottom: "calc(env(safe-area-inset-bottom) + 5rem)" }} />
