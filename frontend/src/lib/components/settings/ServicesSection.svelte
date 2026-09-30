<script lang="ts">
  import AiCard from "./AiCard.svelte";
  import FinnhubCard from "./FinnhubCard.svelte";
  import HomeValuesCard from "./HomeValuesCard.svelte";
  import LogoDevCard from "./LogoDevCard.svelte";
  import { helpCls } from "./ui";

  // Settings → Services: the optional outside services Runway can use, each a row that opens to its key and options.
</script>

<p class={helpCls}>Optional. Each one needs a key of your own, and Runway works without any of them.</p>
<div class="flex flex-col gap-3">
  <AiCard />
  <HomeValuesCard />
  <FinnhubCard />
  <LogoDevCard />
</div>
