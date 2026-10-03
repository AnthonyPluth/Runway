// Settings' tabs, in order, and which one a #setup/… link (or the one you were on last) opens.
export const SECTIONS = [
  { id: "accounts", label: "Accounts" }, { id: "connections", label: "Connections" }, { id: "categories", label: "Categories" },
  { id: "rules", label: "Rules" }, { id: "notifications", label: "Notifications" }, { id: "data", label: "Data" },
];

// Tabs that were renamed or merged into another, so old links still land there: Advanced is now Data (which holds
// Backup too), and the browser extension and the optional services are under Connections.
const ALIASES: Record<string, string> = { advanced: "data", backup: "data", extension: "connections", services: "connections" };

/** The tab to show: the one asked for (or where you were last); until a bank is connected, Connections. */
export function resolveSection(wanted: string, connected: boolean): string {
  const id = ALIASES[wanted] ?? wanted;
  return SECTIONS.some((s) => s.id === id) ? id : connected ? "accounts" : "connections";
}
