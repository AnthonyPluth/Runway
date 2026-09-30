// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import McpCard from "./McpCard.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("the MCP key and the switch for changes", () => {
  it("makes a key, and lets you switch on and off letting assistants change churning", async () => {
    let status = { token: false, token_created: null as string | null, allow_writes: false };
    vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string; body?: { allow?: boolean } }) => {
      if (path === "/api/mcp-key" && !opts?.method) return status;
      if (path === "/api/mcp-key" && opts?.method === "POST") { status = { ...status, token: true, token_created: "2026-09-30T00:00:00" }; return { token: "rwm_read" }; }
      if (path === "/api/mcp-key/writes") { status = { ...status, allow_writes: !!opts?.body?.allow }; return { allow_writes: status.allow_writes }; }
      return { ok: true };
    }) as never);
    render(McpCard);
    const box = await screen.findByRole("checkbox", { name: "Let assistants change churning" });
    expect(box).toBeDisabled();                                        // no key yet
    await userEvent.click(screen.getByRole("button", { name: "Make a key" }));
    expect(await screen.findByLabelText("MCP key")).toHaveValue("rwm_read");
    await waitFor(() => expect(box).toBeEnabled());
    expect(box).not.toBeChecked();                                     // off unless you turn it on
    await userEvent.click(box);
    await waitFor(() => expect(box).toBeChecked());
    const posts = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/mcp-key/writes").map((c) => (c[1] as { body: unknown }).body);
    expect(posts()).toEqual([{ allow: true }]);
    await userEvent.click(box);
    await waitFor(() => expect(posts()).toEqual([{ allow: true }, { allow: false }]));
  });
});
