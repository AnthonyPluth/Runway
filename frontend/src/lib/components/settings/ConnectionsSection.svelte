<script lang="ts">
  import PlaidCard from "./PlaidCard.svelte";
  import SimpleFinCard from "./SimpleFinCard.svelte";
  import type { SettingsAccount } from "./types";

  // Settings → Bank connections: where Runway's bank data comes from (SimpleFIN and Plaid).
  let { accounts }: { accounts: SettingsAccount[] } = $props();
</script>

<SimpleFinCard />
<PlaidCard {accounts} />
