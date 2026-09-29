<script lang="ts">
  import AiCard from "./AiCard.svelte";
  import CartaCard from "./CartaCard.svelte";
  import FinnhubCard from "./FinnhubCard.svelte";
  import LogoDevCard from "./LogoDevCard.svelte";
  import PlaidCard from "./PlaidCard.svelte";
  import HomeValuesCard from "./HomeValuesCard.svelte";
  import RetailCard from "./RetailCard.svelte";
  import SimpleFinCard from "./SimpleFinCard.svelte";
  import type { SettingsAccount } from "./types";

  // Settings → Connections: where Runway's data comes from (banks, stores, Carta) and the optional services it uses.
  let { accounts }: { accounts: SettingsAccount[] } = $props();
</script>

<SimpleFinCard />
<PlaidCard {accounts} />
<RetailCard />
<AiCard />
<CartaCard />
<HomeValuesCard />
<FinnhubCard />
<LogoDevCard />
