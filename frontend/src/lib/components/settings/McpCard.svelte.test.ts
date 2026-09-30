// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import McpCard from "./McpCard.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("the MCP keys", () => {
  it("makes a read key and, separately, a write key, each shown once", async () => {
    let status = { token: false, token_created: null as string | null, write_token: false, write_token_created: null as string | null };
    vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string }) => {
      if (path === "/api/mcp-key" && !opts?.method) return status;
      if (path === "/api/mcp-key" && opts?.method === "POST") { status = { ...status, token: true, token_created: "2026-09-30T00:00:00" }; return { token: "rwm_read" }; }
      if (path === "/api/mcp-key/write") { status = { ...status, write_token: true, write_token_created: "2026-09-30T00:00:00" }; return { token: "rww_write" }; }
      return { ok: true };
    }) as never);
    render(McpCard);
    await userEvent.click(await screen.findByRole("button", { name: "Make a write key" }));
    expect(await screen.findByLabelText("MCP write key")).toHaveValue("rww_write");
    expect(screen.queryByLabelText("MCP key")).not.toBeInTheDocument();            // the read key wasn't made by that
    await userEvent.click(screen.getByRole("button", { name: "Make a key" }));
    expect(await screen.findByLabelText("MCP key")).toHaveValue("rwm_read");
    await waitFor(() => expect(vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/mcp-key/write")).toHaveLength(1));
    expect(screen.getByText(/can't delete anything/)).toBeInTheDocument();
  });
});
