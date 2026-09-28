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
  <main class="min-w-0 flex-1 px-4 py-6 md:px-10 md:py-9">
    <div class="mx-auto max-w-[1280px]">
      {#if app.bootError && !app.state}
        <Card.Root class="mx-auto mt-10 max-w-md">
          <h2 class="text-lg font-semibold text-foreground-strong">Can't reach Runway</h2>
          <p class="mt-1 text-sm text-muted-foreground">{app.bootError}</p>
          <Button class="mt-4" variant="secondary" onclick={boot}>Try again</Button>
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
