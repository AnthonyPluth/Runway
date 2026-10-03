// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/svelte";
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
  vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/rules/preview" ? preview(0) : { ok: true, id: 1 })) as never);
  vi.mocked(toast).mockClear(); vi.mocked(toast.success).mockClear(); vi.mocked(toast.error).mockClear();
  categories.list = [category("Groceries"), category("Coffee")];
});

describe("RuleEditor", () => {
  it("previews how many past transactions the rule would match, after you stop typing", async () => {
    vi.mocked(api).mockImplementation((async () => preview(5, 2)) as never);
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "whole");
    expect(await screen.findByText("Matches 5 past transactions · applying it would change 2")).toBeInTheDocument();
    // The count can already be there from the preview of the empty rule (the mock answers every request alike), so
    // wait for the debounced request that carries what was typed.
    await waitFor(() => expect(posts("/api/rules/preview").at(-1)).toMatchObject({ match: "whole", match_mode: "contains" }));
  });

  it("shows the server's complaint about a bad rule in place of the count", async () => {
    vi.mocked(api).mockImplementation((async () => preview(0, 0, { error: "Give it some text" })) as never);
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "whole");
    expect(await screen.findByText("Give it some text")).toBeInTheDocument();
  });

  it("shows example transactions it would match", async () => {
    vi.mocked(api).mockImplementation((async () => preview(1, 1, { examples: [{ posted: "2026-03-02", amount: -42, payee: "Whole Foods", account_name: "Checking", category: null }] })) as never);
    setup();
    expect(await screen.findByText("Whole Foods")).toBeInTheDocument();
    expect(screen.getByText("-$42.00")).toBeInTheDocument();
  });

  it("adds a rule without touching past transactions unless asked", async () => {
    setup();
    expect(screen.getByRole("checkbox", { name: /Apply to past/ })).not.toBeChecked();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "whole foods");
    await userEvent.selectOptions(screen.getAllByRole("combobox", { name: "Category" })[0], "Groceries");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const body = posts("/api/rules").at(-1)!;
    expect(body).toMatchObject({ match: "whole foods", category: "Groceries", apply: false, split: null });
    await vi.waitFor(() => expect(toast.success).toHaveBeenCalledWith("Rule added", undefined));
    expect(vi.mocked(api).mock.calls.some((c) => String(c[0]).endsWith("/apply"))).toBe(false);
  });

  it("asks before applying a new rule to the past, then saves it, runs it and offers Undo", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => path === "/api/rules/preview" ? preview(4, 2)
      : path === "/api/rules" ? { ok: true, id: 12 }
      : path === "/api/rules/12/apply" ? { updated: 2, changed: [{ id: "t1" }, { id: "t2" }], undoable: true } : {}) as never);
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "whole foods");
    await userEvent.selectOptions(screen.getAllByRole("combobox", { name: "Category" })[0], "Groceries");
    await userEvent.click(screen.getByRole("checkbox", { name: /Apply to past/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    const dialog = await screen.findByRole("dialog", { name: "Apply this rule to past transactions?" });
    expect(dialog).toHaveTextContent("This changes 2 transactions: Groceries.");
    expect(posts("/api/rules")).toHaveLength(0);                       // nothing saved until it's confirmed
    await userEvent.click(within(dialog).getByRole("button", { name: "Change 2 transactions" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/rules/12/apply", { method: "POST" }));
    expect(posts("/api/rules").at(-1)).toMatchObject({ match: "whole foods", apply: false });
    expect(toast).toHaveBeenCalledWith("Rule added · 2 transactions updated", expect.objectContaining({ action: expect.objectContaining({ label: "Undo" }) }));
  });

  it("saves nothing when you cancel the question", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/rules/preview" ? preview(4, 2) : { id: 12 })) as never);
    setup({ id: 9, match: "amazon", category: "Groceries" });
    await userEvent.click(screen.getByRole("checkbox", { name: /Apply to past/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(posts("/api/rules/9")).toHaveLength(0);
    expect(api).not.toHaveBeenCalledWith("/api/rules/9/apply", expect.anything());
    expect(screen.getByRole("button", { name: "Save rule" })).toBeEnabled();
  });

  it("just saves, and says so, when applying to the past would change nothing", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/rules/preview" ? preview(3, 0) : {})) as never);
    setup({ id: 9, match: "amazon", category: "Groceries" });
    await userEvent.click(screen.getByRole("checkbox", { name: /Apply to past/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    await vi.waitFor(() => expect(toast.success).toHaveBeenCalledWith("Rule saved", { description: expect.stringContaining("Nothing to change") }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts("/api/rules/9")).toHaveLength(1);
    expect(api).not.toHaveBeenCalledWith("/api/rules/9/apply", expect.anything());
  });

  it("edits an existing rule, applying it only if asked", async () => {
    setup({ id: 9, match: "amazon", match_mode: "starts", category: "Groceries", review: 1 });
    expect(screen.getByRole("checkbox", { name: /Put it in Review/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Apply to past/ })).not.toBeChecked();
    expect(screen.getByRole("combobox", { name: "How the text matches" })).toHaveValue("starts");
    await userEvent.click(screen.getByRole("button", { name: "Save rule" }));
    expect(posts("/api/rules/9").at(-1)).toMatchObject({ match: "amazon", match_mode: "starts", review: true });
    expect(api).not.toHaveBeenCalledWith("/api/rules/9/apply", expect.anything());
    await vi.waitFor(() => expect(toast.success).toHaveBeenCalledWith("Rule saved", undefined));
  });

  it("needs a condition before it can be saved, and says so once you've started", async () => {
    setup();
    const add = screen.getByRole("button", { name: "Add rule" });
    expect(add).toBeDisabled();
    expect(screen.queryByText(/Add a condition/)).not.toBeInTheDocument();   // not before you've done anything
    await userEvent.selectOptions(screen.getAllByRole("combobox", { name: "Category" })[0], "Groceries");
    const box = screen.getByPlaceholderText("whole foods");
    expect(screen.getByText(/Add a condition/)).toBeInTheDocument();
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(box).toHaveAccessibleDescription(/Add a condition/);
    expect(add).toBeDisabled();
    await userEvent.type(box, "x");
    expect(screen.getByText("Use at least two letters of text.")).toBeInTheDocument();
    await userEvent.type(box, "y");
    expect(box).not.toHaveAttribute("aria-invalid");
    expect(add).toBeEnabled();
  });

  it("takes an amount, a direction or an account as the condition too, and checks the amounts' order", async () => {
    setup();
    await userEvent.selectOptions(screen.getAllByRole("combobox", { name: "Category" })[0], "Groceries");
    const add = screen.getByRole("button", { name: "Add rule" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Account" }), "a1");
    expect(add).toBeEnabled();
    await userEvent.type(screen.getByLabelText("Amount from"), "50");
    await userEvent.type(screen.getByLabelText("Amount up to"), "10");
    expect(screen.getByLabelText("Amount up to")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("The first amount is bigger than the second.")).toBeInTheDocument();
    expect(add).toBeDisabled();
  });

  it("needs something to do", async () => {
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "whole foods");
    expect(screen.getByText(/Choose what the rule does/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add rule" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /Put it in Review/ }));
    expect(screen.getByRole("button", { name: "Add rule" })).toBeEnabled();
  });

  it("splits by percentage instead of one category, and won't save until the parts add up to 100", async () => {
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "costco");
    await userEvent.click(screen.getByRole("button", { name: "Split instead…" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Part 1 category" }), "Groceries");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Part 2 category" }), "Coffee");
    expect(screen.getByText("adds up")).toBeInTheDocument();
    const first = screen.getByRole("spinbutton", { name: "Part 1 percent" });
    await userEvent.clear(first);
    await userEvent.type(first, "30");
    expect(screen.getByText("The parts add up to 80%, not 100%.")).toBeInTheDocument();
    expect(screen.queryByText("adds up")).not.toBeInTheDocument();
    expect(first).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Add rule" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "+ Add a part" }));
    expect(screen.getByRole("spinbutton", { name: "Part 3 percent" })).toHaveValue(20);   // the new part took the missing 20%
    expect(screen.getByText("adds up")).toBeInTheDocument();
    expect(screen.getByText("Give every part a category.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add rule" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Part 3 category" }), "Coffee");
    expect(screen.getByRole("button", { name: "Add rule" })).toBeEnabled();
  });

  it("goes back to a plain rule when a split is cut to one part or switched off", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Split instead…" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Remove this part" })[0]);
    expect(screen.queryByRole("spinbutton", { name: /percent/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Split instead…" }));
    await userEvent.click(screen.getByRole("button", { name: "Don't split" }));
    expect(screen.getByRole("button", { name: "Split instead…" })).toBeInTheDocument();
  });

  it("sends a split as percentages, with no single category", async () => {
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "costco");
    await userEvent.click(screen.getByRole("button", { name: "Split instead…" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Part 1 category" }), "Groceries");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Part 2 category" }), "Coffee");
    await userEvent.click(screen.getByRole("button", { name: "Add rule" }));
    expect(posts("/api/rules").at(-1)).toMatchObject({ category: "", split: [{ category: "Groceries", percent: 50 }, { category: "Coffee", percent: 50 }] });
  });

  it("shows the error and lets you try again when saving fails", async () => {
    vi.mocked(api).mockImplementation((async (path: string) => { if (path === "/api/rules") throw new Error("Duplicate rule"); return preview(0); }) as never);
    setup();
    await userEvent.type(screen.getByPlaceholderText("whole foods"), "xy");
    await userEvent.click(screen.getByRole("checkbox", { name: /Put it in Review/ }));
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
