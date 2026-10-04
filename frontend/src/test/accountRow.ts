import { render } from "@testing-library/svelte";
import { vi } from "vitest";
import { api } from "$lib/api";
import { app, reload } from "$lib/app.svelte";
import AccountRow from "../lib/components/settings/AccountRow.svelte";
import type { SettingsAccount } from "../lib/components/settings/types";
import { toast } from "svelte-sonner";

export const acct = (over: Partial<SettingsAccount> = {}): SettingsAccount => ({ id: "sav", name: "Savings", kind: "savings", balance: 1000, networth_hidden: 0, ...over });
export const show = (a: SettingsAccount) => render(AccountRow, { a, cash: [a], byName: {} });

export const shown = (el: HTMLElement) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.get!.call(el);
export const undoOf = () => {
  const call = vi.mocked(toast).mock.calls.findLast(([, o]) => (o as { action?: unknown })?.action);
  return (call![1] as unknown as { action: { onClick: () => Promise<void> } }).action.onClick;
};

export function resetRow() {
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear(); vi.mocked(reload).mockClear();
  vi.mocked(api).mockReset(); vi.mocked(api).mockResolvedValue({ ok: true } as never);
  app.state = { connected: true, owners: [], primary_account: null };
}
