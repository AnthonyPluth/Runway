<script lang="ts">
  import { app, boot, route } from "$lib/app.svelte";
  import Sidebar from "$lib/components/Sidebar.svelte";
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";
  import { Toaster } from "svelte-sonner";
  import Classic from "./pages/Classic.svelte";
  import Overview from "./pages/Overview.svelte";

  // Pages that have moved to the new app; the rest open in the classic one.
  const PAGES: Record<string, typeof Overview> = { overview: Overview };
</script>

<div class="flex min-h-dvh flex-col md:flex-row">
  <Sidebar />
  <main class="min-w-0 flex-1 p-4 md:p-8">
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
          {@const Page = PAGES[route.page]}
          {#if Page}<Page sub={route.sub} />{:else}<Classic page={route.page} sub={route.sub} />{/if}
        {/key}
      {/if}
    </div>
  </main>
</div>
<Toaster theme="dark" position="bottom-center" />
