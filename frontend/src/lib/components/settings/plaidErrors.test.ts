import { describe, expect, it } from "vitest";
import { plaidProblem } from "./plaidErrors";

describe("a Plaid connection's error, in words", () => {
  it("offers Reconnect only when signing in again fixes it", () => {
    expect(plaidProblem("ITEM_LOGIN_REQUIRED")).toEqual({ text: "Your bank needs you to sign in again", reconnect: true });
    expect(plaidProblem("PENDING_EXPIRATION")).toEqual({ text: "Your bank’s permission expires soon, reconnect to keep syncing", reconnect: true });
    expect(plaidProblem("INVALID_CREDENTIALS").reconnect).toBe(true);
  });

  it("says a passing problem will be retried, without Reconnect", () => {
    expect(plaidProblem("INSTITUTION_DOWN")).toEqual({ text: "The bank isn’t answering right now; Runway will try again", reconnect: false });
    for (const code of ["INSTITUTION_NOT_RESPONDING", "RATE_LIMIT_EXCEEDED", "PRODUCTS_NOT_SUPPORTED"]) {
      const p = plaidProblem(code);
      expect(p.reconnect).toBe(false);
      expect(p.text).not.toContain(code);
    }
  });

  it("quotes anything else", () => {
    expect(plaidProblem("SOMETHING_NEW")).toEqual({ text: "Couldn’t sync: SOMETHING_NEW", reconnect: false });
    expect(plaidProblem("Couldn't reach Plaid: timed out").text).toBe("Couldn’t sync: Couldn't reach Plaid: timed out");
    expect(plaidProblem("toString").reconnect).toBe(false);          // not mistaken for something on the object
  });
});
