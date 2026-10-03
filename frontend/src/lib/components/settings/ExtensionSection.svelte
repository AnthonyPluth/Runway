<script lang="ts">
  import CartaCard from "./CartaCard.svelte";
  import RetailCard from "./RetailCard.svelte";

  // Settings → Connections → Browser extension: installing it once, then what it has imported from each site. Carta's
  // trouble shows on the extension's row too.
  let carta = $state(false);
</script>

<RetailCard problem={carta}>
  <CartaCard onproblem={(on) => (carta = on)} />
</RetailCard>
