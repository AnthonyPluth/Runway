// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/svelte";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("$lib/api", async (orig) => ({ ...(await orig()), api: vi.fn() }));
vi.mock("svelte-sonner", () => ({ toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }));

import { api } from "$lib/api";
import { app } from "$lib/app.svelte";
import ForecastSettings from "./ForecastSettings.svelte";
import { forecastSheet, openForecastSettings } from "./forecastSheet.svelte";

const accounts = [
  { id: "chk", name: "Everyday Checking", kind: "checking" },
  { id: "sav", name: "High-Yield Savings", kind: "savings" },
  { id: "visa", name: "Rewards Visa", kind: "credit" },
];

beforeEach(() => {
  vi.mocked(api).mockReset();
  vi.mocked(api).mockImplementation(async (path: string) => (path === "/api/accounts" ? accounts : path === "/api/state" ? { connected: true, primary_account: "sav", horizon_days: 60 } : {}));
  app.state = { connected: true, primary_account: "chk", horizon_days: 90 };
});
afterEach(() => { cleanup(); forecastSheet.open = false; document.body.style.pointerEvents = ""; });

const open = async () => {
  const user = userEvent.setup();
  render(ForecastSettings, { label: "Everyday Checking" });
  await user.click(screen.getByRole("button", { name: "Forecast settings" }));
  await screen.findByRole("dialog", { name: "Forecast settings" });
  return user;
};

describe("ForecastSettings", () => {
  it("opens from the account name and lists only cash accounts", async () => {
    await open();
    await waitFor(() => expect(screen.getByRole("option", { name: "High-Yield Savings" })).toBeInTheDocument());
    expect(screen.queryByRole("option", { name: "Rewards Visa" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Primary account")).toHaveValue("chk");
    expect(screen.getByLabelText("Default forecast length (days)")).toHaveValue(90);
  });

  it("saves the primary account", async () => {
    const user = await open();
    await waitFor(() => expect(screen.getByRole("option", { name: "High-Yield Savings" })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Primary account"), "sav");
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { primary_account: "sav" } }));
    await waitFor(() => expect(app.state?.primary_account).toBe("sav"));
  });

  it("saves the forecast length and tells Overview, which loads it without drawing the page afresh", async () => {
    const onhorizon = vi.fn();
    const version = app.version;
    const user = userEvent.setup();
    render(ForecastSettings, { label: "Everyday Checking", onhorizon });
    await user.click(screen.getByRole("button", { name: "Forecast settings" }));
    const days = await screen.findByLabelText("Default forecast length (days)");
    await user.clear(days);
    await user.type(days, "60");
    await user.tab();
    await waitFor(() => expect(api).toHaveBeenCalledWith("/api/settings", { method: "POST", body: { horizon_days: 60 } }));
    await waitFor(() => expect(onhorizon).toHaveBeenCalledWith(60));
    expect(app.version).toBe(version);
    expect(screen.getByRole("dialog", { name: "Forecast settings" })).toBeInTheDocument();
  });

  it("opens from elsewhere on Overview, with its accounts", async () => {
    render(ForecastSettings, { label: "Everyday Checking" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    openForecastSettings();
    await screen.findByRole("dialog", { name: "Forecast settings" });
    await waitFor(() => expect(screen.getByRole("option", { name: "High-Yield Savings" })).toBeInTheDocument());
  });

  it("changes the account and reloads Overview's figures without closing", async () => {
    const onchange = vi.fn();
    const user = userEvent.setup();
    render(ForecastSettings, { label: "Everyday Checking", onchange });
    await user.click(screen.getByRole("button", { name: "Forecast settings" }));
    await waitFor(() => expect(screen.getByRole("option", { name: "High-Yield Savings" })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Primary account"), "sav");
    await waitFor(() => expect(onchange).toHaveBeenCalled());
    expect(forecastSheet.open).toBe(true);
    expect(screen.getByRole("dialog", { name: "Forecast settings" })).toBeInTheDocument();
  });

  it("has no everyday-spending setting", async () => {
    const user = userEvent.setup();
    render(ForecastSettings, { label: "Checking" });
    await user.click(screen.getByRole("button", { name: "Forecast settings" }));
    expect(await screen.findByLabelText("Primary account")).toBeInTheDocument();
    expect(screen.queryByText(/everyday spending/i)).not.toBeInTheDocument();
  });
});
