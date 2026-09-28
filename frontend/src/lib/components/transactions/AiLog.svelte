<script lang="ts" module>
  // Open or closed stays as you left it when the page loads again.
  let open = $state(false);
</script>

<script lang="ts">
  import { api } from "$lib/api";
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";
  import type { AiLogRow } from "./types";

  // Every request Runway made to the AI (the Suggest button, automatic runs during sync, order items), newest first.
  let rows = $state<Promise<AiLogRow[]>>(get());
  function get() { return api<AiLogRow[]>("/api/ai/log", { keep: true }); }

  /** Load the log again; `show` opens it (after a failed request, so you see why). */
  export function refresh(show = false) { if (show) open = true; rows = get(); }

  const when = (at: string, seconds = false) => new Date(at.replace(" ", "T")).toLocaleString("en-US",
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) });
  const what = (p: string) => (p === "review" ? "Suggest button" : p === "orders" ? "Amazon and Target items" : "Automatic, during sync");
</script>

<Card.Root class="mb-6 py-0">
  <details bind:open class="group">
    <summary class="cursor-pointer px-6 py-4 text-sm">
      <b class="font-semibold">AI activity</b>
      <span class="text-xs text-muted-foreground">
        {#await rows then list}
          {@const last = list[0]}
          {#if last}
            · last {last.purpose === "review" ? "run" : last.purpose === "orders" ? "order items run" : "automatic run"} {when(last.at)}: {last.ok ? `${last.answered} of ${last.merchants} suggested` : "failed"}
          {:else}· nothing yet{/if}
        {/await}
      </span>
    </summary>
    <div class="px-6 pb-4 text-xs text-muted-foreground">
      {#await rows}
        Loading…
      {:then list}
        {#if list.length}
          <div class="overflow-x-auto">
            <table class="w-full">
              <thead>
                <tr class="text-left"><th class="py-1.5 pr-2 font-medium">When</th><th class="px-2 py-1.5 font-medium">What</th><th class="px-2 py-1.5 font-medium">Model</th>
                  <th class="px-2 py-1.5 text-right font-medium">Result</th><th class="py-1.5 pl-2 text-right font-medium">Time</th></tr>
              </thead>
              <tbody>
                {#each list as r (r.id)}
                  <tr class="border-t align-top">
                    <td class="whitespace-nowrap py-1.5 pr-2">{when(r.at, true)}</td>
                    <td class="px-2 py-1.5 text-foreground">{what(r.purpose)}
                      <div class={cn("text-muted-foreground", !r.ok && "text-destructive")}>{r.ok ? "" : "▲ "}{r.message || ""}</div>
                      {#if r.reply}
                        <details><summary class="cursor-pointer">What the model said</summary>
                          <pre class="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-muted p-2">{r.reply}</pre></details>
                      {/if}
                    </td>
                    <td class="px-2 py-1.5"><code>{r.model || ""}</code></td>
                    <td class="px-2 py-1.5 text-right tabular-nums">{r.ok ? `${r.answered}/${r.merchants}` : "error"}</td>
                    <td class="py-1.5 pl-2 text-right tabular-nums">{r.seconds != null ? `${r.seconds}s` : ""}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {:else}
          <p>No AI requests yet. Click “Suggest categories with AI” and each request will show up here.</p>
        {/if}
      {:catch err}
        {err.message}
      {/await}
    </div>
  </details>
</Card.Root>
