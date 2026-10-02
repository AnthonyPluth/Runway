<script lang="ts">
  import { api } from "$lib/api";
  import { cn } from "$lib/utils";
  import type { AiLogRow } from "./types";

  // Every request Runway made to the AI (the Suggest button, automatic runs during sync, order items), newest first:
  // the diagnostics under Settings → Connections → AI categorization. Nothing is shown until there's a request.
  let rows = $state<AiLogRow[]>([]);
  api<AiLogRow[]>("/api/ai/log", { keep: true }).then((r) => (rows = r), () => {});

  const when = (at: string) => {
    const d = new Date(at.replace(" ", "T"));
    return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
  };
  const what = (p: string) => (p === "review" ? "Suggest button" : p === "orders" ? "Amazon and Target items" : "Automatic, during sync");
</script>

{#if rows.length}
  <div class="overflow-x-auto text-xs text-muted-foreground">
    <table class="w-full" aria-label="AI requests">
      <thead>
        <tr class="text-left"><th class="py-1.5 pr-2 font-medium">When</th><th class="px-2 py-1.5 font-medium">What</th><th class="px-2 py-1.5 font-medium">Model</th>
          <th class="px-2 py-1.5 text-right font-medium">Result</th><th class="py-1.5 pl-2 text-right font-medium">Time</th></tr>
      </thead>
      <tbody>
        {#each rows as r (r.id)}
          <tr class="border-t align-top">
            <td class="whitespace-nowrap py-1.5 pr-2">{when(r.at)}</td>
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
{/if}
