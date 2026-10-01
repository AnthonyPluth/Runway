import { describe, expect, it } from "vitest";
import { SECTIONS, resolveSection } from "./sections";

describe("Settings tabs", () => {
  it("lists Accounts first, then Connections (which holds the optional services too), and Advanced last", () => {
    expect(SECTIONS.map((s) => s.id)).toEqual(["accounts", "connections", "categories", "rules", "notifications", "assumptions", "advanced"]);
    expect(SECTIONS.find((s) => s.id === "connections")?.label).toBe("Connections");
  });

  it("sends the old #setup/backup link to Advanced, and #setup/extension and #setup/services to Connections, and keeps the other ids", () => {
    expect(resolveSection("backup", true)).toBe("advanced");
    expect(resolveSection("extension", true)).toBe("connections");
    expect(resolveSection("connections", true)).toBe("connections");
    expect(resolveSection("services", false)).toBe("connections");
  });

  it("opens on Accounts, or on Connections until a bank is connected", () => {
    expect(resolveSection("", true)).toBe("accounts");
    expect(resolveSection("", false)).toBe("connections");
    expect(resolveSection("nonsense", false)).toBe("connections");
  });
});
