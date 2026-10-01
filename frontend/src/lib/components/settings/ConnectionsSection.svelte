<script lang="ts">
  import PlaidCard from "./PlaidCard.svelte";
  import SimpleFinCard from "./SimpleFinCard.svelte";
  import type { SettingsAccount } from "./types";

  // Settings → Connections: the services Runway brings your data in through, each with its own setup. SimpleFIN and Plaid
  // here; which provider each of your accounts uses, and which bank account it is, is decided under Accounts. The browser
  // extension's card follows (Settings.svelte).
  let { accounts = [] }: { accounts?: SettingsAccount[] } = $props();
</script>

<SimpleFinCard {accounts} />
<PlaidCard />
