<script lang="ts">
  import AiCard from "./AiCard.svelte";
  import ExtensionSection from "./ExtensionSection.svelte";
  import FinnhubCard from "./FinnhubCard.svelte";
  import HomeValuesCard from "./HomeValuesCard.svelte";
  import LogoDevCard from "./LogoDevCard.svelte";
  import PlaidCard from "./PlaidCard.svelte";
  import SimpleFinCard from "./SimpleFinCard.svelte";
  import type { SettingsAccount } from "./types";
  import { helpCls } from "./ui";

  // Settings → Connections: every outside service Runway talks to, each a row that opens to its setup. Your banks come in
  // through SimpleFIN or Plaid (which provider each account uses, and which bank account it is, is decided under
  // Accounts); the browser extension and the services with a key of your own are optional.
  let { accounts = [] }: { accounts?: SettingsAccount[] } = $props();
  const heading = "text-xs font-medium uppercase tracking-wide text-muted-foreground";
</script>

<section class="flex flex-col gap-3" aria-labelledby="connections-banks">
  <h2 id="connections-banks" class={heading}>Banks</h2>
  <SimpleFinCard {accounts} />
  <PlaidCard />
</section>

<section class="flex flex-col gap-3" aria-labelledby="connections-optional">
  <div class="flex flex-col gap-1">
    <h2 id="connections-optional" class={heading}>Optional</h2>
    <p class={helpCls}>Each needs a key or setup of your own, and Runway works without any of them.</p>
  </div>
  <ExtensionSection />
  <AiCard />
  <HomeValuesCard />
  <FinnhubCard />
  <LogoDevCard />
</section>
