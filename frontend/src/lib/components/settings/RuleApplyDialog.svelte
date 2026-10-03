<script lang="ts">
  import { ConfirmDialog } from "$lib/components/ui/confirm-dialog";
  import { plural } from "$lib/format";
  import type { RuleApplyAsk } from "./ruleApply.svelte";

  // "Apply this rule to past transactions?", for the Rules list's Apply and for saving a rule with Apply ticked.
  let { q }: { q: RuleApplyAsk } = $props();
</script>

<ConfirmDialog bind:open={q.open} title="Apply this rule to past transactions?" confirmLabel={`Change ${plural(q.changes, "transaction")}`} busyLabel="Applying…"
  description={`This changes ${plural(q.changes, "transaction")}${q.what ? `: ${q.what}` : ""}. Categories you picked yourself stay as they are. You can undo it afterwards.`}
  onconfirm={() => q.go()} />
