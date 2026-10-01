<script lang="ts">
  import { api } from "$lib/api";
  import { fmtDateTime } from "$lib/format";
  import type { AiLogRow } from "./types";

  // One line under the Suggest button: when the AI last ran and how it went. Every request is listed under
  // Settings → Connections → AI categorization. Loading again keeps the line on screen until the new one arrives.
  let last = $state<AiLogRow | null>(null);
  let seq = 0;
  function get() {
    const n = ++seq;
    api<AiLogRow[]>("/api/ai/log", { keep: true }).then((r) => { if (n === seq) last = r[0] ?? null; }, () => {});
  }
  get();

  /** Load the log again, after a request. */
  export function refresh(_failed = false) { get(); }

  const what = (p: string) => (p === "review" ? "run" : p === "orders" ? "order items run" : "automatic run");
</script>

{#if last}
  <p class="mb-4 text-xs text-muted-foreground" title="Every AI request is listed in Settings → Connections → AI categorization">
    Last {what(last.purpose)} {fmtDateTime(new Date(last.at.replace(" ", "T")))}: {last.ok ? `${last.answered} of ${last.merchants} suggested` : "failed"}
  </p>
{/if}
