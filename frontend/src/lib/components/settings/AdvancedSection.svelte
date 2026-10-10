<script lang="ts">
  import AppLockCard from "./AppLockCard.svelte";
  import BackupSection from "./BackupSection.svelte";
  import McpCard from "./McpCard.svelte";

  // Settings → Data (the tab was called Advanced, and #setup/advanced still opens it): who can get at your data. This
  // device's app lock, Backup & restore, then the AI assistants that can reach your data over MCP.
</script>

<AppLockCard />
<BackupSection />
<McpCard />
