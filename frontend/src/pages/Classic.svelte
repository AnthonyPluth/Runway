<script lang="ts">
  import { Button } from "$lib/components/ui/button";
  import * as Card from "$lib/components/ui/card";

  // A page that hasn't moved to the new app yet: open it in the classic one.
  let { page, sub = "" }: { page: string; sub?: string } = $props();
  const names: Record<string, string> = { transactions: "Transactions", review: "Review", budget: "Budget", recurring: "Recurring",
    reports: "Reports", investments: "Investments", networth: "Net worth", setup: "Settings" };
</script>

<Card.Root class="mx-auto mt-10 max-w-md text-center">
  <h2 class="text-lg font-semibold text-foreground-strong">{names[page] ?? "This page"} is coming soon</h2>
  <p class="mt-2 text-sm text-muted-foreground">It hasn't moved to the new app yet. Use it in the classic app in the meantime.</p>
  <Button class="mt-5" href={`/#${page}${sub ? "/" + sub : ""}`}>Open in the classic app</Button>
</Card.Root>
