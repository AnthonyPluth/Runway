// #setup/accounts?account=<id> opens that account's row in Settings → Accounts at its Statement section (Overview's
// "Enter Visa's latest statement" links here). The row picks the id up when it's drawn, which may be after the accounts
// load; the hash goes back to #setup/accounts, so the same link works again and a reload doesn't reopen it.
export const accountFocus = $state({ id: "" });

function fromHash(): void {
  const [path, query = ""] = location.hash.slice(1).split("?");
  if (path !== "setup/accounts" && path !== "settings/accounts") return;
  const id = new URLSearchParams(query).get("account");
  if (!id) return;
  accountFocus.id = id;
  history.replaceState(history.state, "", "#setup/accounts");
}
fromHash();
window.addEventListener("hashchange", fromHash);
