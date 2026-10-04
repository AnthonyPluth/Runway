// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn(), session: {} }));
vi.mock("svelte-sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));
vi.mock("$lib/categories.svelte", () => ({
  loadCategories: vi.fn(async () => []),
  categories: { list: [] },
  catLabel: (x: string) => x,
  catLook: () => ({ color: "#888" }),
  categoryGroups: () => [],
  catParentOf: () => null,
}));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import Settings from "./Settings.svelte";

const serve = (fail: string[] = [], hold: string[] = []) =>
  vi.mocked(api).mockImplementation((async (path: string) => {
    if (hold.includes(path)) return new Promise(() => {});
    if (fail.includes(path)) throw new Error(`${path} broke`);
    if (path === "/api/accounts")
      return [{ id: "chk", name: "Checking", kind: "checking", balance: 100 }];
    if (path === "/api/rules" || path === "/api/accounts/deleted") return [];
    if (path === "/api/plaid/status")
      return { configured: false, env: "production", client_id: "", items: [] };
    if (path === "/api/mcp-settings")
      return {
        connections: [],
        keys: [],
        allow_writes: false,
        allow_categorize: false,
        allow_all: false,
      };
    if (path === "/api/retail") throw new Error("not in this test");
    return {};
  }) as never);

beforeEach(() => {
  vi.mocked(api).mockReset();
  app.state = { connected: true, brands: {} } as never;
});

describe("Settings, tab by tab", () => {
  it("still shows Accounts and Advanced when the rules don't load", async () => {
    serve(["/api/rules"]);
    const { unmount } = render(Settings, { sub: "accounts" });
    expect(await screen.findByText("Checking")).toBeInTheDocument();
    expect(screen.queryByText(/didn’t load/)).toBeNull();
    unmount();
    render(Settings, { sub: "advanced" });
    expect(await screen.findAllByText(/Backup/)).not.toHaveLength(0);
    expect(screen.queryByText(/didn’t load/)).toBeNull();
  });

  it("says so on the Rules tab, with a way to try again", async () => {
    serve(["/api/rules"]);
    render(Settings, { sub: "rules" });
    expect(
      await screen.findByText("This tab didn’t load. The others still work."),
    ).toBeInTheDocument();
    expect(screen.getByText("/api/rules broke")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });

  it("shows Connections at once, without waiting for the accounts", async () => {
    serve([], ["/api/accounts"]);
    render(Settings, { sub: "connections" });
    expect(
      await screen.findByRole("heading", { name: "Banks" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: /Loading/ })).toBeNull();
  });

  it("asks only for what the tab shows", async () => {
    serve();
    render(Settings, { sub: "accounts" });
    await screen.findByText("Checking");
    const asked = vi.mocked(api).mock.calls.map(([p]) => p);
    expect(asked.filter((p) => p === "/api/accounts")).toHaveLength(1);
  });

  it("waits with a skeleton shaped like a list of rows", async () => {
    serve([], ["/api/accounts"]);
    render(Settings, { sub: "accounts" });
    const waiting = (await screen.findByText("Loading…")).closest(
      "[role=status]",
    )!;
    expect(waiting.querySelectorAll(".group-list > .cell")).toHaveLength(5);
    expect(screen.queryByText("Checking")).toBeNull();
  });
});
