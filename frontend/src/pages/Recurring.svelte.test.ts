// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import Recurring from "./Recurring.svelte";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation((async (path: string) => (path === "/api/accounts" || path === "/api/recurring" ? [] : {})) as never);
  app.state = { connected: true };
});

describe("Recurring page", () => {
  it("has its own heading and the recurring items, with no tabs and no month picker", async () => {
    render(Recurring);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Recurring");
    expect((await screen.findAllByRole("button", { name: "Add" })).length).toBeGreaterThan(0);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Previous month" })).not.toBeInTheDocument();
  });

  it("asks you to connect a bank first", () => {
    app.state = { connected: false };
    render(Recurring);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Recurring");
    expect(screen.getByText("Connect a bank to track your bills and income")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add" })).not.toBeInTheDocument();
  });
});
