<script lang="ts">
  import * as Card from "$lib/components/ui/card";
  import { cn } from "$lib/utils";

  // A headline number (Money in, Spent, Left over), styled like the Overview's tiles.
  let { label, value, sub = "", alert = false }: { label: string; value: string; sub?: string; alert?: boolean } = $props();
</script>

<Card.Root class={cn("gap-2", alert && "border-destructive")}>
  <Card.Header>
    <Card.Description class={cn(alert && "text-destructive")}>{label}</Card.Description>
    <Card.Title class={cn("text-2xl tabular-nums", alert && "text-destructive")}>{value}</Card.Title>
  </Card.Header>
  <Card.Content class="min-h-5 text-sm text-muted-foreground">{sub}</Card.Content>
</Card.Root>
