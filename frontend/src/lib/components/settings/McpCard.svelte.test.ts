// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { toast } from "svelte-sonner";
import McpCard from "./McpCard.svelte";

beforeEach(() => { vi.mocked(api).mockReset(); });

type Status = {
  allow_writes: boolean; allow_categorize: boolean; oauth: boolean; url: string | null; reason: string | null;
  connections: { id: number; client: string | null; who: string | null; scope: string[]; created: string | null; last_used: string | null }[];
};

function serve(status: Status) {
  vi.mocked(api).mockImplementation((async (path: string, opts?: { method?: string; body?: { allow?: boolean } }) => {
    if (path === "/api/mcp-settings") return status;
    if (path === "/api/mcp-settings/writes") { status.allow_writes = !!opts?.body?.allow; return { allow_writes: status.allow_writes }; }
    if (path === "/api/mcp-settings/categorize") { status.allow_categorize = !!opts?.body?.allow; return { allow_categorize: status.allow_categorize }; }
    const m = path.match(/^\/api\/mcp-settings\/connections\/(\d+)\/revoke$/);
    if (m) { status.connections = status.connections.filter((c) => c.id !== Number(m[1])); return { ok: true }; }
    throw new Error(`unexpected ${path}`);
  }) as never);
}

describe("AI assistants (MCP)", () => {
  it("shows the address to connect to and how, with no key to make", async () => {
    serve({ allow_writes: false, allow_categorize: false, oauth: true, url: "https://runway.example.com/mcp", reason: null, connections: [] });
    render(McpCard);
    expect(await screen.findByLabelText("MCP address")).toHaveValue("https://runway.example.com/mcp");
    expect(screen.getByLabelText("MCP address")).toHaveAttribute("title", expect.stringContaining("claude mcp add --transport http runway https://runway.example.com/mcp"));
    expect(screen.queryByText("None yet.")).not.toBeInTheDocument();
    expect(screen.queryByText(/Make a key/)).not.toBeInTheDocument();
  });

  it("says why assistants can't connect yet", async () => {
    serve({ allow_writes: false, allow_categorize: false, oauth: false, url: null, reason: "Set RUNWAY_PUBLIC_URL to the address you open Runway at.", connections: [] });
    render(McpCard);
    expect(await screen.findByText(/Set RUNWAY_PUBLIC_URL/)).toBeInTheDocument();
    expect(screen.getByLabelText("MCP address")).toHaveValue(`${location.origin}/mcp`);
  });

  it("lists connected assistants and revokes one", async () => {
    serve({ allow_writes: true, allow_categorize: false, oauth: true, url: "https://r.example/mcp", reason: null, connections: [
      { id: 1, client: "Claude", who: "me@example.com", scope: ["read", "churning:write"], created: "2026-09-01T10:00:00", last_used: null },
      { id: 2, client: null, who: null, scope: ["read"], created: "2026-09-02T10:00:00", last_used: "2026-09-03T10:00:00" },
      { id: 3, client: "Cat", who: null, scope: ["read", "categorize:write"], created: "2026-09-02T10:00:00", last_used: "2026-09-03T10:00:00" },
      { id: 4, client: "Both", who: null, scope: ["read", "churning:write", "categorize:write"], created: "2026-09-02T10:00:00", last_used: "2026-09-03T10:00:00" },
    ] });
    render(McpCard);
    expect(await screen.findByText("Claude")).toBeInTheDocument();
    expect(screen.getByText("Read + churning")).toBeInTheDocument();
    expect(screen.getByText("Unnamed app")).toBeInTheDocument();
    expect(screen.getByText("Read")).toBeInTheDocument();
    expect(screen.getByText("Read + categorizing")).toBeInTheDocument();
    expect(screen.getByText("Read + churning + categorizing")).toBeInTheDocument();
    expect(screen.getByText(/by me@example.com/)).toBeInTheDocument();
    expect(screen.getByText(/not used yet/)).toBeInTheDocument();
    const revoke = screen.getAllByRole("button", { name: "Disconnect" })[0];
    await userEvent.click(revoke);                                   // asks first
    await userEvent.click(screen.getByRole("button", { name: "Disconnect?" }));
    await waitFor(() => expect(screen.queryByText("Claude")).not.toBeInTheDocument());
    expect(vi.mocked(api)).toHaveBeenCalledWith("/api/mcp-settings/connections/1/revoke", { method: "POST" });
    expect(screen.getByText("Unnamed app")).toBeInTheDocument();
  });

  it("switches letting assistants change churning on and off", async () => {
    serve({ allow_writes: false, allow_categorize: false, oauth: true, url: "https://r.example/mcp", reason: null, connections: [] });
    render(McpCard);
    const box = await screen.findByRole("checkbox", { name: "Let assistants change churning" });
    expect(box).toBeEnabled();
    expect(box).not.toBeChecked();                                   // off unless you turn it on
    await userEvent.click(box);
    await waitFor(() => expect(box).toBeChecked());
    const posts = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/mcp-settings/writes").map((c) => (c[1] as { body: unknown }).body);
    expect(posts()).toEqual([{ allow: true }]);
    await userEvent.click(box);
    await waitFor(() => expect(posts()).toEqual([{ allow: true }, { allow: false }]));
  });

  it("switches letting assistants categorize on and off, apart from churning", async () => {
    serve({ allow_writes: false, allow_categorize: false, oauth: true, url: "https://r.example/mcp", reason: null, connections: [] });
    render(McpCard);
    const box = await screen.findByRole("checkbox", { name: "Let assistants categorize" });
    expect(box).not.toBeChecked();                                   // off unless you turn it on
    await userEvent.click(box);
    await waitFor(() => expect(box).toBeChecked());
    const posts = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/mcp-settings/categorize").map((c) => (c[1] as { body: unknown }).body);
    expect(posts()).toEqual([{ allow: true }]);
    expect(screen.getByRole("checkbox", { name: "Let assistants change churning" })).not.toBeChecked();
    await userEvent.click(box);
    await waitFor(() => expect(posts()).toEqual([{ allow: true }, { allow: false }]));
  });

  it("says under each switch what it lets an assistant do", async () => {
    serve({ allow_writes: false, allow_categorize: false, oauth: true, url: "https://r.example/mcp", reason: null, connections: [] });
    render(McpCard);
    expect(await screen.findByRole("checkbox", { name: "Let assistants change churning" }))
      .toHaveAccessibleDescription("Mark benefits used; add or update cards, benefits, to-dos and plans. Never deletes.");
    expect(screen.getByRole("checkbox", { name: "Let assistants categorize" }))
      .toHaveAccessibleDescription("Set or accept the category of a transaction or order item. No deleting, splitting or renaming.");
    expect(screen.getByRole("link", { name: "How to connect" })).toHaveAttribute("href", "https://anthonypluth.github.io/Runway/using/mcp/");
  });

  it("selects the address and says how to copy it where the browser has no clipboard (plain http)", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    serve({ allow_writes: false, allow_categorize: false, oauth: true, url: "http://192.168.1.5:8000/mcp", reason: null, connections: [] });
    render(McpCard);
    const box = await screen.findByLabelText("MCP address");
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(box).toHaveFocus();
    expect((box as HTMLInputElement).selectionEnd).toBe("http://192.168.1.5:8000/mcp".length);
    expect(toast).toHaveBeenCalledWith(expect.stringMatching(/^Selected\. /));
  });
});
