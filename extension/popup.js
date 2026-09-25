// Popup: connect to the backend and launch the ImmoScout24 search.

// The pre-built search URL (3+ rooms, <=1100 warm, floor 2-3, 5km around
// Kassel). When this tab loads, the content script auto-detects it as a
// search page and offers to send the listings to your dashboard.
const SEARCH_URL =
  "https://www.immobilienscout24.de/Suche/radius/wohnung-mieten" +
  "?centerofsearchaddress=Kassel&numberofrooms=3.0-&price=-1100.0" +
  "&pricetype=calculatedtotalrent&floor=2-3&geocoordinates=51.31292%3B9.49829%3B5.0";

const backendInput = document.getElementById("backend");
const statusEl = document.getElementById("status");

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = cls || "";
}

// Restore any saved backend URL.
chrome.storage.local.get("backend").then(({ backend }) => {
  if (backend) backendInput.value = backend;
});

document.getElementById("connect").addEventListener("click", async () => {
  const backend = backendInput.value.trim().replace(/\/$/, "");
  await chrome.storage.local.set({ backend });
  setStatus("Connecting…");
  chrome.runtime.sendMessage({ type: "PING" }, (res) => {
    if (res && res.ok) {
      setStatus(
        `Connected ✓ — ${res.data.listings} listings, ${res.data.pending_actions} pending actions.`,
        "ok"
      );
    } else {
      setStatus(
        "Connection failed: " + (res ? res.error : "no response") +
        ". Is the backend running?",
        "err"
      );
    }
  });
});

document.getElementById("search").addEventListener("click", () => {
  chrome.tabs.create({ url: SEARCH_URL });
  setStatus("Opened ImmoScout24 search. Log in if asked, then use the green bar to send listings.", "ok");
});
