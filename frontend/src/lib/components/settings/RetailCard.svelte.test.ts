// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));
vi.mock("$lib/app.svelte", () => ({ app: { state: {}, version: 0 }, refreshState: vi.fn() }));

import { api } from "$lib/api";
import RetailCard from "./RetailCard.svelte";

const store = { name: "Amazon", orders: 0, matched: 0, unmatched: 0, last: null };
const retail = (token: boolean) => ({ token, token_created: "2026-09-01T10:00:00", ai: false, recent: [],
  stores: { amazon: store, target: { ...store, name: "Target" }, costco: { ...store, name: "Costco" } } });

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("the browser extension's install steps", () => {
  it("are open until there's a key, then folded away", async () => {
    vi.mocked(api).mockResolvedValue(retail(false) as never);
    const { unmount } = render(RetailCard);
    const steps = (await screen.findByText("Install steps")).closest("details")!;
    expect(steps.open).toBe(true);
    unmount();
    vi.mocked(api).mockResolvedValue(retail(true) as never);
    render(RetailCard);
    expect((await screen.findByText("Install steps")).closest("details")!.open).toBe(false);
    expect(screen.getByRole("button", { name: "Make a new key" })).toBeInTheDocument();
  });
});

describe("the browser extension's key", () => {
  it("says it doesn't expire, and when it ends", async () => {
    vi.mocked(api).mockResolvedValue({ ...retail(true), token_problem: null } as never);
    render(RetailCard);
    const line = (await screen.findByText(/^Key made/)).textContent!;
    expect(line).toContain("not used yet.");
    expect(line).toContain("It doesn't expire: it works until you make a new one or remove it, or until whoever made it can no longer sign in.");
    expect(line).not.toMatch(/works until \w+ \d/);   // no date it runs out
    expect(screen.queryByText(/expired/)).toBeNull();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
  });

  it("asks for a new one when its maker can no longer sign in", async () => {
    vi.mocked(api).mockResolvedValue({ ...retail(true), token_problem: "owner_gone" } as never);
    render(RetailCard);
    expect(await screen.findByText("The person who made this key can no longer sign in: make a new one.")).toBeInTheDocument();
    expect(screen.getByText("New key needed")).toBeInTheDocument();
  });
});
