// Kassel Agent — background service worker.
// The ONLY component that talks to the local backend. Content scripts and the
// popup send it messages; it holds the backend URL and does the fetches
// (it has host_permissions for localhost, so these aren't blocked by CORS).

const DEFAULT_BACKEND = "http://localhost:8000";

async function backendUrl() {
  const { backend } = await chrome.storage.local.get("backend");
  return backend || DEFAULT_BACKEND;
}

async function api(path, options) {
  const base = await backendUrl();
  const res = await fetch(base + path, options);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}${text ? " — " + text.slice(0, 200) : ""}`);
  }
  return res.json();
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "PING") {
        sendResponse({ ok: true, data: await api("/api/extension/ping") });

      } else if (msg.type === "INGEST") {
        const data = await api("/api/listings/ingest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ listings: msg.listings }),
        });
        sendResponse({ ok: true, data });

      } else if (msg.type === "GET_DRAFT") {
        const q = new URLSearchParams({ address: msg.address || "", title: msg.title || "" });
        sendResponse({ ok: true, data: await api("/api/application-draft?" + q.toString()) });

      } else {
        sendResponse({ ok: false, error: "Unknown message type: " + msg.type });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.message ? e.message : e) });
    }
  })();
  return true; // keep the channel open for the async sendResponse
});
