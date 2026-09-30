// Whether Overview's forecast settings sheet is open. Kept here rather than in the sheet, so anything can open it (the
// getting-started checklist, the line under the verdict) and it stays open while Overview loads its figures again.
export const forecastSheet = $state({ open: false });

export function openForecastSettings(): void {
  forecastSheet.open = true;
}

// #overview?forecast opens it from anywhere (Settings → Accounts, the README). The hash goes back to #overview, so
// the same link works again and a reload doesn't reopen it; leaving Overview closes it.
function fromHash(): void {
  const [path, query = ""] = location.hash.slice(1).split("?");
  if ((path.split("/")[0] || "overview") !== "overview") { forecastSheet.open = false; return; }
  if (!query.split("&").includes("forecast")) return;
  forecastSheet.open = true;
  history.replaceState(history.state, "", "#overview");
}
fromHash();
window.addEventListener("hashchange", fromHash);
