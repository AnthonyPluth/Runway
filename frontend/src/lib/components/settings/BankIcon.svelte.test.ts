// @vitest-environment jsdom
import { fireEvent, render } from "@testing-library/svelte";
import { beforeEach, describe, expect, it } from "vitest";

import { app } from "$lib/app.svelte";
import BankIcon, { logoFor } from "./BankIcon.svelte";

beforeEach(() => { app.state = null; });

describe("a bank's icon", () => {
  it("names Logo.dev's logo the way the server keys it", () => {
    expect(logoFor("Wealthfront")).toBe("/api/merchants/brand%3Awealthfront/logo");
    expect(logoFor("  E*TRADE   from Morgan Stanley ")).toBe("/api/merchants/brand%3Ae*trade%20from%20morgan%20stanley/logo");
  });

  it("uses the logo Runway has for an account, else its letter", () => {
    app.state = { connected: true, brands: { a1: { institution: "Wealthfront", src: "/api/merchants/brand%3Awealthfront/logo", initial: "W" }, a2: { institution: "Vestwell", src: null, initial: "V" } } } as never;
    const one = render(BankIcon, { id: "a1" });
    expect(one.container.querySelector("img")).toHaveAttribute("src", "/api/merchants/brand%3Awealthfront/logo");
    const two = render(BankIcon, { id: "a2" });
    expect(two.container.querySelector("img")).toBeNull();
    expect(two.getByTitle("Vestwell")).toHaveTextContent("V");
  });

  it("asks for a connection's logo by name only with a Logo.dev key, and shows its letter when it isn't there", async () => {
    const off = render(BankIcon, { name: "Fidelity" });
    expect(off.container.querySelector("img")).toBeNull();
    expect(off.container).toHaveTextContent("F");
    app.state = { connected: true, logodev_configured: true, brands: {} } as never;
    const on = render(BankIcon, { name: "Fidelity" });
    const img = on.container.querySelector("img")!;
    expect(img).toHaveAttribute("src", "/api/merchants/brand%3Afidelity/logo");
    await fireEvent.error(img);                                          // not fetched yet
    expect(on.container.querySelector("img")).toBeNull();
    expect(on.container).toHaveTextContent("F");
  });

  it("uses the logo the state has for a connection, by website for a bank Runway knows", () => {
    app.state = { connected: true, logodev_configured: true, connection_logos: { "Citibank Online": "/api/merchants/site%3Aciti.com/logo", Vestwell: null } } as never;
    expect(render(BankIcon, { name: "Citibank Online" }).container.querySelector("img")).toHaveAttribute("src", "/api/merchants/site%3Aciti.com/logo");
    const none = render(BankIcon, { name: "Vestwell" });
    expect(none.container.querySelector("img")).toBeNull();
    expect(none.container).toHaveTextContent("V");
  });
});
