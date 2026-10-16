// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/components/investments/InvestmentsView.svelte", async () => ({
  default: (await import("../test/InvestmentsViewStub.svelte")).default,
}));

import Investments from "./Investments.svelte";

describe("Investments page", () => {
  it("shows its heading above the Investments view", () => {
    render(Investments);
    expect(screen.getByRole("heading", { level: 1, name: "Investments" })).toBeInTheDocument();
    expect(screen.getByTestId("investments-view")).toBeInTheDocument();
  });
});
