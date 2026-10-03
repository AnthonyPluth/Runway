// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import AssetForm from "./AssetForm.svelte";
import type { NetWorth } from "./types";

const d = (configured: boolean) => ({ loan_accounts: [], realie: { configured, used: 0, limit: 25 } }) as unknown as NetWorth;
const posts = () => vi.mocked(api).mock.calls.filter((c) => c[0] === "/api/assets");

beforeEach(() => { vi.mocked(api).mockReset(); });

describe("adding an asset", () => {
  it("adds with Enter, says what's missing first, and doesn't add it twice while saving", async () => {
    let finish: () => void = () => {};
    vi.mocked(api).mockImplementation((() => new Promise((r) => { finish = () => r({ ok: true }); })) as never);
    const onclose = vi.fn();
    render(AssetForm, { a: null, d: d(true), kind: "vehicle", onclose });
    const user = userEvent.setup();
    const name = screen.getByLabelText("Name");
    await user.type(name, "{Enter}");
    expect(screen.getByText("Give it a name")).toBeInTheDocument();
    expect(screen.getByText("Enter what it’s worth today")).toBeInTheDocument();
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(posts()).toHaveLength(0);

    await user.type(name, "Old Civic");
    expect(screen.queryByText("Give it a name")).toBeNull();
    await user.type(screen.getByLabelText(/^Value today/), "8000{Enter}");
    expect(screen.getByRole("button", { name: "Adding…" })).toBeDisabled();
    await user.type(name, "{Enter}");
    expect(posts()).toHaveLength(1);
    expect((posts()[0][1] as { body: Record<string, unknown> }).body).toMatchObject({ name: "Old Civic", kind: "vehicle", value: "8000" });
    finish();
    await waitFor(() => expect(onclose).toHaveBeenCalledWith(true));
  });

  it("shows a refusal under the form and keeps what was typed", async () => {
    vi.mocked(api).mockRejectedValue(new Error("Value can't be negative"));
    const onclose = vi.fn();
    render(AssetForm, { a: null, d: d(true), kind: "other", onclose });
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Name"), "Boat");
    await user.type(screen.getByLabelText(/^Value today/), "5");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Value can't be negative");
    expect(screen.getByLabelText("Name")).toHaveValue("Boat");
    expect(screen.getByRole("button", { name: "Add" })).toBeEnabled();
    expect(onclose).not.toHaveBeenCalled();
  });

  it("says why Realie's weekly update can't be ticked, and names the yearly change plainly", () => {
    render(AssetForm, { a: null, d: d(false), kind: "home", onclose: vi.fn() });
    const box = screen.getByRole("checkbox", { name: /Update from Realie weekly/ });
    expect(box).toBeDisabled();
    expect(box.closest("label")).toHaveTextContent("needs a Realie key in Settings");
    expect(screen.getByLabelText(/^Expected yearly change/)).toBeInTheDocument();
  });
});
