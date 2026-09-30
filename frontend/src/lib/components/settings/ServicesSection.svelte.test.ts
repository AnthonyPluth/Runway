// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", () => ({ api: vi.fn(), newPage: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import { SECTIONS, resolveSection } from "./sections";
import ServicesSection from "./ServicesSection.svelte";

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockResolvedValue({});
  app.state = { connected: true };
});

const row = (name: string) => screen.getByText(name).closest("details")!;

describe("Services rows", () => {
  it("are collapsed, and say On or Not set for each service", () => {
    app.state = { connected: true, has_api_key: true, finnhub_configured: true };
    render(ServicesSection);
    for (const name of ["AI categorization", "Home values", "Live stock prices", "Merchant and bank logos"]) expect(row(name)).not.toHaveAttribute("open");
    expect(within(row("AI categorization")).getByText("On")).toBeInTheDocument();
    expect(within(row("Live stock prices")).getByText("On")).toBeInTheDocument();
    expect(within(row("Home values")).getByText("Not set")).toBeInTheDocument();
    expect(within(row("Merchant and bank logos")).getByText("Not set")).toBeInTheDocument();
  });

  it("open to the key field, and never show a saved key", async () => {
    app.state = { connected: true, realie_configured: true };
    render(ServicesSection);
    expect(within(row("Home values")).getByText("On")).toBeInTheDocument();
    await userEvent.click(within(row("Home values")).getByText("Home values"));
    expect(row("Home values")).toHaveAttribute("open");
    const key = within(row("Home values")).getByLabelText("Realie API key");
    expect(key).toHaveAttribute("type", "password");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "•••••••• saved");
  });
});

describe("Settings tabs", () => {
  it("lists Accounts first, and Bank connections, Browser extension, Services and Advanced", () => {
    expect(SECTIONS.map((s) => s.id)).toEqual(["accounts", "connections", "categories", "rules", "extension", "services", "notifications", "advanced"]);
  });

  it("sends the old #setup/backup link to Advanced, and keeps the other ids", () => {
    expect(resolveSection("backup", true)).toBe("advanced");
    expect(resolveSection("connections", true)).toBe("connections");
    expect(resolveSection("extension", false)).toBe("extension");
  });

  it("opens on Accounts, or on Bank connections until a bank is connected", () => {
    expect(resolveSection("", true)).toBe("accounts");
    expect(resolveSection("", false)).toBe("connections");
    expect(resolveSection("nonsense", false)).toBe("connections");
  });
});
