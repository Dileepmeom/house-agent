// Kassel Agent — content script. Runs on immobilienscout24.de pages inside
// YOUR logged-in Chrome. Two jobs:
//   1. On a search-results page: scrape the visible listing cards and offer
//      to send them to your Kassel Agent dashboard.
//   2. On a single listing (expose) page: add an "Insert my application"
//      button that fills the contact form with your German draft. It never
//      clicks send — you review and send yourself.

(function () {
  const BAR_ID = "kassel-agent-bar";

  // --- Scraper: pull listing cards out of a search-results page. -----------
  // Heuristic (resilient to ImmoScout's changing CSS class names): find every
  // link to an /expose/<id>, walk up to the surrounding card, and read price /
  // size / rooms / floor out of the German text.
  function scrapeSearchResults() {
    // Anchor on the real result cards, not the /expose/ link — that link sits
    // inside the card's image gallery, several levels below the details.
    const cards = Array.from(document.querySelectorAll(
      '.listing-card, [class*="result-list__listing"], article[data-item="result"]'
    ));
    const num = (s) => (s ? parseFloat(s.replace(/\./g, "").replace(",", ".")) : null);
    const seen = new Set();
    const listings = [];

    for (const card of cards) {
      const text = (card.textContent || "").replace(/\s+/g, " ");
      // Skip promoted new-build ads — they ignore the location filter and have
      // "from X €" ranges that aren't real warm rents.
      if (/Bauprojekt|zum Projekt/i.test(text)) continue;

      const a = card.querySelector('a[href*="/expose/"]');
      if (!a) continue;
      const m = a.href.match(/expose\/(\d+)/);
      if (!m) continue;
      const id = m[1];
      if (seen.has(id)) continue;
      seen.add(id);

      // Pick the first euro amount in a plausible rent range (avoids gluing a
      // house number onto the price).
      const euros = [...text.matchAll(/([\d.]+(?:,\d{2})?)\s*€/g)]
        .map((x) => num(x[1])).filter((v) => v != null);
      const warm = euros.find((v) => v >= 200 && v <= 3000) ?? null;

      const sizeM = text.match(/([\d.]+(?:,\d+)?)\s*m²/);
      const roomsM = text.match(/([\d.]+(?:,\d+)?)\s*Zi/);
      const floorM = text.match(/(\d+)\.\s*(?:OG|Obergeschoss|Etage|Stock)/i);

      let address = null;
      const addrM = text.match(/km\s*\|\s*([^|]+?)(?:geprüfte|Neu\b|$)/i);
      if (addrM) address = addrM[1].replace(/,\s*Kassel.*/, ", Kassel").trim().slice(0, 80);

      const title = (card.querySelector("h2, h3")?.textContent || "")
        .replace(/\s+/g, " ").trim().slice(0, 200) || null;

      listings.push({
        source_id: id,
        link: a.href.split("?")[0],
        listing_title: title,
        address: address,
        rent_display: warm != null ? warm.toLocaleString("de-DE") + " €" : null,
        rent_warm: warm,
        size_sqm: num(sizeM && sizeM[1]),
        rooms: num(roomsM && roomsM[1]),
        floor_number: floorM ? parseInt(floorM[1], 10) : null, // usually absent on search cards
      });
    }
    return listings;
  }

  function isSearchPage() {
    return /\/Suche\//.test(location.pathname) || /wohnung-mieten/.test(location.pathname);
  }
  function isExposePage() {
    return /\/expose\/\d+/.test(location.pathname);
  }

  // --- Floating status bar shared by both modes. ---------------------------
  function makeBar() {
    let bar = document.getElementById(BAR_ID);
    if (bar) return bar;
    bar = document.createElement("div");
    bar.id = BAR_ID;
    bar.style.cssText = [
      "position:fixed", "z-index:2147483647", "bottom:18px", "right:18px",
      "background:#0f766e", "color:#fff", "font:13px -apple-system,Segoe UI,sans-serif",
      "padding:12px 14px", "border-radius:10px", "box-shadow:0 4px 16px rgba(0,0,0,.25)",
      "max-width:320px", "line-height:1.4",
    ].join(";");
    document.body.appendChild(bar);
    return bar;
  }

  function button(label, onclick) {
    const b = document.createElement("button");
    b.textContent = label;
    b.style.cssText = [
      "margin-top:8px", "margin-right:6px", "background:#fff", "color:#0f766e",
      "border:none", "border-radius:6px", "padding:6px 10px", "font-weight:600",
      "cursor:pointer", "font-size:12px",
    ].join(";");
    b.addEventListener("click", onclick);
    return b;
  }

  function send(type, payload) {
    return new Promise((resolve) =>
      chrome.runtime.sendMessage({ type, ...payload }, resolve)
    );
  }

  // --- Mode 1: search results ----------------------------------------------
  // Auto-captures the moment the results load — you don't click anything.
  async function initSearchBar() {
    const bar = makeBar();

    // ImmoScout loads results lazily, so give the cards a moment to appear and
    // retry a few times before giving up.
    let listings = [];
    for (let attempt = 0; attempt < 6; attempt++) {
      listings = scrapeSearchResults();
      if (listings.length) break;
      bar.innerHTML = `<b>Kassel Agent</b><br>Looking for listings… (${attempt + 1})`;
      await new Promise((r) => setTimeout(r, 1500));
    }

    if (!listings.length) {
      bar.innerHTML =
        `<b>Kassel Agent</b><br>No listings detected yet.<br>` +
        `<small>If you can see apartments on the page, the layout may have ` +
        `changed — tell your agent and it'll retune. Or scroll down and click Retry.</small>`;
      bar.appendChild(button("Retry", () => initSearchBar()));
      return;
    }

    bar.innerHTML = `<b>Kassel Agent</b><br>Found ${listings.length} — sending automatically…`;
    const res = await send("INGEST", { listings });
    if (res && res.ok) {
      bar.innerHTML =
        `<b>Kassel Agent</b><br>Captured ✓ — ${res.data.new} new, ` +
        `${res.data.drafted} drafted for review.<br>` +
        `<small>They're on your dashboard now.</small>`;
    } else {
      bar.innerHTML = `<b>Kassel Agent</b><br>Auto-send failed: ${res ? res.error : "no response"}` +
        `<br><small>Is the backend running on localhost:8000?</small>`;
      bar.appendChild(button("Retry", () => initSearchBar()));
    }
  }

  // --- Mode 2: single expose page ------------------------------------------
  function initExposeBar() {
    const id = (location.pathname.match(/expose\/(\d+)/) || [])[1];
    const title = (document.querySelector("h1") || {}).textContent || "";
    const bar = makeBar();
    bar.innerHTML = `<b>Kassel Agent</b><br>Listing ${id}`;

    // Save this listing to the dashboard.
    bar.appendChild(button("Save listing", async () => {
      const res = await send("INGEST", {
        listings: [{
          source_id: id,
          link: location.href.split("?")[0],
          listing_title: title.trim().slice(0, 200) || null,
        }],
      });
      bar.innerHTML = res && res.ok
        ? `<b>Kassel Agent</b><br>Saved ✓ (${res.data.new} new)`
        : `<b>Kassel Agent</b><br>Failed: ${res ? res.error : "no response"}`;
    }));

    // Fill the contact form with the German application draft.
    bar.appendChild(button("Insert my application", async () => {
      const textarea = document.querySelector(
        'textarea[name="message"], textarea[data-testid="message-textarea"], textarea'
      );
      if (!textarea) {
        alert("No message box found on this page yet. Open the contact form first, then click again.");
        return;
      }
      const res = await send("GET_DRAFT", { title: title.trim() });
      if (!res || !res.ok) {
        alert("Couldn't get the draft: " + (res ? res.error : "no response"));
        return;
      }
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
      setter.call(textarea, res.data.message);
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.focus();
      bar.innerHTML = `<b>Kassel Agent</b><br>Application inserted ✓<br>` +
        `<small>Review it, then click ImmoScout's own send button.</small>`;
    }));
  }

  // Boot.
  try {
    if (isExposePage()) initExposeBar();
    else if (isSearchPage()) initSearchBar();
  } catch (e) {
    console.error("[Kassel Agent] content script error:", e);
  }
})();
