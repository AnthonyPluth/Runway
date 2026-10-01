// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";

import OwnerSelect from "./OwnerSelect.svelte";

describe("owner select", () => {
  it("offers the people alphabetically, keeps an old name that's set, and leaves out Joint", () => {
    render(OwnerSelect, { owners: ["Alex", "Sam", "Joint"], value: "Pat" });
    expect(within(screen.getByRole("combobox")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Alex", "Pat", "Sam"]);
  });
});
