// @vitest-environment jsdom
import { fireEvent, render } from "@testing-library/svelte";
import { beforeEach, describe, expect, it } from "vitest";

import { app } from "$lib/app.svelte";
import TickerIcon from "./TickerIcon.svelte";

beforeEach(() => { app.state = null; });

describe("a holding's icon", () => {
  it("shows its letter without a Logo.dev key", () => {
    const r = render(TickerIcon, { ticker: "vti", name: "Vanguard Total Market" });
    expect(r.container.querySelector("img")).toBeNull();
    expect(r.container).toHaveTextContent("V");
  });

  it("asks Runway for the logo by ticker, and falls back to the letter when there isn't one", async () => {
    app.state = { connected: true, logodev_configured: true } as never;
    const r = render(TickerIcon, { ticker: "brk.b", name: "Berkshire" });
    const img = r.container.querySelector("img")!;
    expect(img).toHaveAttribute("src", "/api/merchants/ticker%3ABRK.B/logo");
    await fireEvent.error(img);
    expect(r.container.querySelector("img")).toBeNull();
    expect(r.container).toHaveTextContent("B");
  });

  it("never asks for cash (CUR:USD) or a holding with no ticker", () => {
    app.state = { connected: true, logodev_configured: true } as never;
    expect(render(TickerIcon, { ticker: "CUR:USD", name: "Cash" }).container.querySelector("img")).toBeNull();
    expect(render(TickerIcon, { ticker: null, name: "Private fund" }).container.querySelector("img")).toBeNull();
  });
});
