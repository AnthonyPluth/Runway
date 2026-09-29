// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { categories } from "$lib/categories.svelte";
import { toast } from "svelte-sonner";
import { category } from "../../../test/fixtures";
import RuleEditor from "./RuleEditor.svelte";
import type { Rule, SettingsAccount } from "./types";

const accounts = [{ id: "a1", name: "Checking", kind: "checking" }] as SettingsAccount[];
type Body = Record<string, unknown>;
const posts = (path: string) => vi.mocked(api).mock.calls.filter((c) => c[0] === path).map((c) => (c[1] as { body?: Body }).body);
const setup = (rule: Rule | null = null) => {
  const onclose = vi.fn();
  render(RuleEditor, { rule, accounts, onclose });
  return onclose;
};
const preview = (matches: number, changes = matches, extra = {}) => ({ matches, changes, ...extra });

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/rules/preview" ? preview(0) : { updated: 0 })) as never);
  categories.list = [category("Groceries"), category("Coffee")];
});

describe("RuleEditor", () => {
  it("previews how many past transactions the rule would match, after you stop typing", async () => {
    vi.mocked(api).mockImplementation((async () => preview(5, 2)) as never);
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "whole");
    expect(await screen.findByText("Matches 5 past transactions · applying it would change 2")).toBeInTheDocument();
    expect(posts("/api/rules/preview").at(-1)).toMatchObject({ match: "whole", match_mode: "contains" });
  });

  it("shows the server's complaint about a bad rule in place of the count", async () => {
    vi.mocked(api).mockImplementation((async () => preview(0, 0, { error: "Give it some text" })) as never);
    setup();
    expect(await screen.findByText("Give it some text")).toBeInTheDocument();
  });

  it("shows example transactions it would match", async () => {
    vi.mocked(api).mockImplementation((async () => preview(1, 1, { examples: [{ posted: "2026-03-02", amount: -42, payee: "Whole Foods", account_name: "Checking", category: null }] })) as never);
    setup();
    expect(await screen.findByText("Whole Foods")).toBeInTheDocument();
    expect(screen.getByText("-$42.00")).toBeInTheDocument();
  });

  it("adds a rule and applies it to past transactions by default", async () => {
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "whole foods");
    await userEvent.selectOptions(screen.getAllByRole("combobox", { name: "Category" })[0], "Groceries");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const body = posts("/api/rules").at(-1)!;
    expect(body).toMatchObject({ match: "whole foods", category: "Groceries", apply: true, split: null });
    expect(toast.success).toHaveBeenCalledWith("Rule added");
  });

  it("reports how many transactions it updated", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/rules" ? { updated: 1 } : preview(1))) as never);
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    await vi.waitFor(() => expect(toast.success).toHaveBeenCalledWith("Rule added · 1 transaction updated"));
  });

  it("edits an existing rule, applying it only if asked", async () => {
    setup({ id: 9, match: "amazon", match_mode: "starts", category: "Groceries", review: 1 });
    expect(screen.getByRole("checkbox", { name: /Put it in Review/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Apply to past/ })).not.toBeChecked();
    expect(screen.getByRole("combobox", { name: "How the text matches" })).toHaveValue("starts");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    expect(posts("/api/rules/9").at(-1)).toMatchObject({ match: "amazon", match_mode: "starts", review: true });
    expect(api).not.toHaveBeenCalledWith("/api/rules/9/apply", expect.anything());
    await vi.waitFor(() => expect(toast.success).toHaveBeenCalledWith("Rule saved"));
  });

  it("applies an edited rule to the past when ticked", async () => {
    setup({ id: 9, match: "amazon" });
    await userEvent.click(screen.getByRole("checkbox", { name: /Apply to past/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/api/rules/9/apply", { method: "POST" }));
  });

  it("splits by percentage instead of one category, and says when the parts don't add up to 100", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Split instead…" }));
    expect(screen.getByText("adds up")).toBeInTheDocument();
    const [first] = screen.getAllByRole("spinbutton", { name: "Percent" });
    await userEvent.clear(first);
    await userEvent.type(first, "30");
    expect(screen.getByText("80% of 100%")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "+ Add a part" }));
    expect(screen.getAllByRole("spinbutton", { name: "Percent" })).toHaveLength(3);
    expect(screen.getByText("adds up")).toBeInTheDocument();   // the new part took the missing 20%
  });

  it("goes back to a plain rule when a split is cut to one part or switched off", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Split instead…" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Remove this part" })[0]);
    expect(screen.queryByRole("spinbutton", { name: "Percent" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Split instead…" }));
    await userEvent.click(screen.getByRole("button", { name: "Don't split" }));
    expect(screen.getByRole("button", { name: "Split instead…" })).toBeInTheDocument();
  });

  it("sends a split as percentages, with no single category", async () => {
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "costco");
    await userEvent.click(screen.getByRole("button", { name: "Split instead…" }));
    const cats = screen.getAllByRole("combobox", { name: "Category" });
    await userEvent.selectOptions(cats[1], "Groceries")   // [0] is the (now disabled) single category;
    await userEvent.selectOptions(cats[2], "Coffee");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    expect(posts("/api/rules").at(-1)).toMatchObject({ category: "", split: [{ category: "Groceries", percent: 50 }, { category: "Coffee", percent: 50 }] });
  });

  it("shows the error and lets you try again when saving fails", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => { if (path === "/api/rules") throw new Error("Duplicate rule"); return preview(0); }) as never);
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    await vi.waitFor(() => expect(toast.error).toHaveBeenCalledWith("Duplicate rule"));
    expect(screen.getByRole("button", { name: "Add rule" })).toBeEnabled();
  });

  it("closes on Cancel", async () => {
    const onclose = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onclose).toHaveBeenCalled();
  });
});
