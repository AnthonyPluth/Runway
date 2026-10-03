// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig<typeof import("$lib/api")>()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { toast } from "svelte-sonner";
import { category } from "../../../test/fixtures";
import RulesSection from "./RulesSection.svelte";
import type { Rule, SettingsAccount } from "./types";

const rule: Rule = { id: 4, match: "whole foods", match_mode: "contains", category: "Groceries", rename: "Whole Foods", review: 0, split: null, summary: "merchant contains 'whole foods'" };
const accounts = [{ id: "a1", name: "Checking", kind: "checking" }] as SettingsAccount[];
const posts = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path);
const row = () => screen.getByText("merchant contains 'whole foods'").closest("div")!.parentElement!;

type Reply = { preview?: unknown; apply?: unknown };
const serve = ({ preview = { matches: 3, changes: 3 }, apply = { updated: 3, changed: [], undoable: true } }: Reply = {}) =>
  vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/rules/preview" ? preview : path.endsWith("/apply") ? apply : {})) as never);

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  categories.list = [category("Groceries")];
  render(RulesSection, { rules: [rule], accounts });
});

describe("Rules → Apply", () => {
  it("counts what it would change and asks before running the rule over history", async () => {
    serve({ preview: { matches: 9, changes: 7 } });
    await userEvent.click(within(row()).getByRole("button", { name: "Apply" }));
    const dialog = await screen.findByRole("dialog", { name: "Apply this rule to past transactions?" });
    expect(dialog).toHaveTextContent("This changes 7 transactions");
    expect(dialog).toHaveTextContent("Groceries · rename to Whole Foods");
    expect(posts("/api/rules/preview")[0][1]).toMatchObject({ body: { match: "whole foods", category: "Groceries", rename: "Whole Foods" } });
    expect(posts("/api/rules/4/apply")).toHaveLength(0);   // not until it's confirmed
    await userEvent.click(within(dialog).getByRole("button", { name: "Change 7 transactions" }));
    await waitFor(() => expect(posts("/api/rules/4/apply")).toHaveLength(1));
  });

  it("changes nothing when you cancel", async () => {
    serve();
    await userEvent.click(within(row()).getByRole("button", { name: "Apply" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(posts("/api/rules/4/apply")).toHaveLength(0);
  });

  it("just says so, with no dialog, when nothing would change", async () => {
    serve({ preview: { matches: 0, changes: 0 } });
    await userEvent.click(within(row()).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("No past transactions match this rule"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    serve({ preview: { matches: 2, changes: 0 } });
    await userEvent.click(within(row()).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.stringContaining("Nothing to change")));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows why a rule can't be previewed", async () => {
    serve({ preview: { error: "Unknown category: Nope", matches: 0, changes: 0 } });
    await userEvent.click(within(row()).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Unknown category: Nope"));
  });

  it("offers an undo that puts each transaction back as it was", async () => {
    const changed = [{ id: "t1", was_category: null, was_source: null, was_confidence: null, was_needs_review: 1, was_payee: "WHOLEFDS", was_split: 0 }];
    serve({ apply: { updated: 1, changed, undoable: true } });
    await userEvent.click(within(row()).getByRole("button", { name: "Apply" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /^Change/ }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("1 transaction updated", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) })));
    const { action } = vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } };
    await action.onClick();
    expect(posts("/api/transactions/bulk")[0][1]).toEqual({ method: "POST", body: { restore: [
      { id: "t1", category: null, category_source: null, confidence: null, needs_review: 1, payee: "WHOLEFDS", is_split: 0 }] } });
  });

  it("says it can't be undone when the server didn't keep what changed", async () => {
    serve({ apply: { updated: 5000, changed: [], undoable: false } });
    await userEvent.click(within(row()).getByRole("button", { name: "Apply" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /^Change/ }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("5000 transactions updated", { description: "That’s too many to undo from here." }));
  });
});

describe("Rules → Remove", () => {
  it("removes a rule at once, and Undo adds it back as it was", async () => {
    const split = { ...rule, id: 5, category: null, rename: null, amount_min: 10, direction: "out" as const, split: [{ category: "Groceries", percent: 60 }, { category: "Coffee", percent: 40 }] };
    vi.mocked(api).mockResolvedValue({ ok: true, id: 6 } as never);
    cleanup();   // this one, not the list beforeEach drew
    render(RulesSection, { rules: [split], accounts });
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/rules/5", { method: "DELETE" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Rule removed", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) })));
    const { action } = vi.mocked(toast).mock.calls.at(-1)![1] as unknown as { action: { onClick: () => Promise<void> } };
    await action.onClick();
    expect(posts("/api/rules")[0][1]).toEqual({ method: "POST", body: {
      match: "whole foods", match_mode: "contains", amount_min: 10, amount_max: undefined, direction: "out", account_id: undefined,
      category: null, rename: null, review: 0, split: split.split, apply: false } });
  });

  it("shows the error, and offers no Undo, when it can't be removed", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Rule not found"));
    await userEvent.click(within(row()).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Rule not found"));
    expect(toast).not.toHaveBeenCalled();
  });
});

describe("Rules tab", () => {
  it("says which rule wins when several match", () => {
    expect(screen.getByText("When several rules match, the most specific one wins.")).toBeInTheDocument();
  });

  it("lists the rules under a heading, below the page's title", () => {
    expect(screen.getByRole("heading", { level: 2, name: "Rules" })).toBeInTheDocument();
  });
});
