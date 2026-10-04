// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  history.replaceState(null, "", "#overview");
  vi.resetModules();
});

describe("opening the forecast settings from a link", () => {
  it("opens for #overview?forecast and puts the address back to #overview", async () => {
    history.replaceState(null, "", "#overview?forecast");
    const { forecastSheet } = await import("./forecastSheet.svelte");
    expect(forecastSheet.open).toBe(true);
    expect(location.hash).toBe("#overview");
    forecastSheet.open = false;
    location.hash = "overview?forecast";
    await vi.waitFor(() => expect(forecastSheet.open).toBe(true));
    expect(location.hash).toBe("#overview");
  });

  it("stays shut on a plain #overview, and closes when you leave Overview", async () => {
    history.replaceState(null, "", "#overview");
    const { forecastSheet, openForecastSettings } =
      await import("./forecastSheet.svelte");
    expect(forecastSheet.open).toBe(false);
    openForecastSettings();
    location.hash = "budget";
    await vi.waitFor(() => expect(forecastSheet.open).toBe(false));
  });
});
