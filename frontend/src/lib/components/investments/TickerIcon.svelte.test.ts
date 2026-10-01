// @vitest-environment jsdom
import { fireEvent, render } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";

import TickerIcon from "./TickerIcon.svelte";

describe("a holding's icon", () => {
  it("shows its letter when the API gave no logo", () => {
    const r = render(TickerIcon, { ticker: "vti", name: "Vanguard Total Market", logo: null });
    expect(r.container.querySelector("img")).toBeNull();
    expect(r.container).toHaveTextContent("V");
  });

  it("shows the logo the API gave, and falls back to the letter if it won't load", async () => {
    const r = render(TickerIcon, { ticker: "brk.b", name: "Berkshire", logo: "/api/merchants/ticker%3ABRK.B/logo" });
    const img = r.container.querySelector("img")!;
    expect(img).toHaveAttribute("src", "/api/merchants/ticker%3ABRK.B/logo");
    await fireEvent.error(img);
    expect(r.container.querySelector("img")).toBeNull();
    expect(r.container).toHaveTextContent("B");
  });

  it("uses the first letter of the name for a holding with no usable ticker", () => {
    expect(render(TickerIcon, { ticker: "CUR:USD", name: "Cash" }).container).toHaveTextContent("C");
    expect(render(TickerIcon, { ticker: null, name: "Private fund" }).container).toHaveTextContent("P");
  });
});
