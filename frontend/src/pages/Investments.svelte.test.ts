// @vitest-environment jsdom
import { render, screen } from "@testing-library/svelte";
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/components/investments/InvestmentsView.svelte", async () => ({
  default: (await import("../test/InvestmentsViewStub.svelte")).default,
}));
vi.mock("$lib/components/investments/HoldingDetail.svelte", async () => ({
  default: (await import("../test/HoldingDetailStub.svelte")).default,
}));

import Investments from "./Investments.svelte";

describe("Investments page", () => {
  it("shows its heading above the Investments view", () => {
    render(Investments);
    expect(screen.getByRole("heading", { level: 1, name: "Investments" })).toBeInTheDocument();
    expect(screen.getByTestId("investments-view")).toBeInTheDocument();
    expect(screen.queryByTestId("holding-detail")).not.toBeInTheDocument();
  });

  it("shows one holding's page at #investments/holding/<id>, with the id decoded", () => {
    render(Investments, { sub: "holding/s%2F1" });
    expect(screen.getByTestId("holding-detail")).toHaveTextContent("s/1");
    expect(screen.queryByTestId("investments-view")).not.toBeInTheDocument();
  });
});
