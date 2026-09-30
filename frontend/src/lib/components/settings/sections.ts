// Settings' tabs, in order, and which one a #setup/… link (or the one you were on last) opens.
export const SECTIONS = [
  { id: "accounts", label: "Accounts" }, { id: "connections", label: "Bank connections" }, { id: "categories", label: "Categories" },
  { id: "rules", label: "Rules" }, { id: "extension", label: "Browser extension" }, { id: "services", label: "Services" },
  { id: "notifications", label: "Notifications" }, { id: "advanced", label: "Advanced" },
];

// Tabs that were merged into another: Backup is now under Advanced, and old #setup/backup links still land there.
const ALIASES: Record<string, string> = { backup: "advanced" };

/** The tab to show: the one asked for (or where you were last); until a bank is connected, Bank connections. */
export function resolveSection(wanted: string, connected: boolean): string {
  const id = ALIASES[wanted] ?? wanted;
  return SECTIONS.some((s) => s.id === id) ? id : connected ? "accounts" : "connections";
}
