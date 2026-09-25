# 🏠 Kassel Wohnungssuche Agent

A personal apartment-search assistant for Kassel, Germany. It captures ImmoScout24
listings into a local dashboard, enriches them with floor / move-in / district data,
drafts formal German applications, tracks landlord replies from Gmail, and helps you
apply — **with you in control of every message that actually gets sent.**

> 🔒 **Your personal data stays out of git.** The applicant profile (name, phone,
> salary, email) lives in `config/profile.json`, which is **git-ignored** — only the
> placeholder `config/profile.example.json` is committed. `config/credentials.json`
> and the SQLite database are ignored too. So this repo is safe to keep public; just
> never force-add anything under `config/` except the `.example` file.

---

## What it does

1. **Capture** — a Chrome extension reads ImmoScout24 search results *from your own
   logged-in browser session* and sends them to your local dashboard. No headless
   bot, no login automation — you browse as yourself, the extension rides along.
2. **Enrich** — floor number, floor type (ground / middle / top), district, and
   move-in date come from each listing's detail page.
3. **Filter** — listings on the 4th floor or above are flagged; ground-floor and
   ≤3rd-floor matches get a German application auto-drafted for your review.
4. **Review & send** — you approve (and optionally edit) each draft on the dashboard,
   then use the extension's **autofill** to drop the message into ImmoScout24's
   contact form. **You click send.** Nothing is sent to a landlord automatically.
5. **Track replies** — an optional Gmail integration reads landlord responses,
   classifies them (viewing invite / docs request / question / offer / rejection),
   and queues the ones needing your input.

### Design principle: human-in-the-loop

The agent automates everything *up to* the moment a message reaches a real person.
Searching, capturing, enriching, drafting, tracking — all automatic. **Sending an
application to a landlord always requires your explicit click.** This is deliberate,
not a missing feature.

---

## Project structure

```
house-agent/
├── README.md                     # This file
├── requirements.txt              # Python dependencies
├── .gitignore
│
├── src/                          # Python backend
│   ├── database.py               # SQLite schema + helpers (listings, applications,
│   │                             #   action_queue, email_events, sync_state)
│   ├── application_template.py   # German message templates (loads config/profile.json)
│   ├── api.py                    # FastAPI backend + serves the dashboard
│   │                             #   (endpoints: /api/dashboard, /api/listings/ingest,
│   │                             #    /api/application-draft, /api/sync, /api/extension/ping …)
│   ├── seed.py                   # Seed the DB with your existing tracked applications
│   ├── draft_new.py              # Draft applications for any listing without one yet
│   ├── scraper_immoscout.py      # (Optional) Playwright scraper + manual send helper
│   ├── gmail_checker.py          # (Optional) Gmail API reader + reply classifier
│   ├── login_immoscout.py        # (Optional) one-time interactive ImmoScout login
│   └── cron_runner.py            # (Optional) scheduled scrape + email check
│
├── dashboard/
│   └── index.html                # The dashboard UI (map, listings table with Floor /
│                                 #   Move-in / District, action queue, Sync button)
│
├── extension/                    # Chrome extension (Manifest V3)
│   ├── manifest.json
│   ├── background.js             # Talks to the local backend (the only fetcher)
│   ├── content.js                # Scrapes ImmoScout pages, autofills the contact form
│   ├── popup.html
│   └── popup.js                  # "Connect" + "Search new properties"
│
├── config/                       # Personal data & secrets (git-ignored)
│   ├── profile.example.json      # Committed template — copy to profile.json
│   ├── profile.json              # ⚠️ Your real profile (git-ignored, you create it)
│   └── credentials.json          # Gmail OAuth — you add this (git-ignored)
│
└── data/                         # Runtime state (git-ignored, auto-created)
    ├── kassel.db                 # SQLite database
    └── browser_profile/          # Playwright session (only if you use the scraper)
```

## How it works (data flow)

```
   You browse ImmoScout24 (logged in as yourself, in Chrome)
                 │
        ┌────────▼─────────┐
        │ Chrome extension │  content.js scrapes the results page
        │  (content.js)    │──────────────┐
        └────────┬─────────┘              │ POST /api/listings/ingest
                 │ autofill on            ▼
                 │ contact form   ┌─────────────────┐
                 │ (you click     │  FastAPI (api.py)│
                 │  send)         │  + SQLite DB     │
                 │                └────────┬─────────┘
                 │                         │ GET /api/dashboard
                 │                ┌────────▼─────────┐
                 └───────────────▶│  Dashboard       │  map, listings table,
                                  │ (dashboard/…)    │  draft review + approve
                                  └──────────────────┘
   Gmail (optional) ── gmail_checker.py ──▶ same DB ──▶ dashboard "Needs your input"
```

---

## Setup (clone & run)

### Prerequisites
- **Python 3.11+**
- **Google Chrome** (for the extension)

### 1. Clone and install

```bash
git clone git@github.com:Dileepmeom/house-agent.git
cd house-agent
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Add your applicant profile

Your personal details live in `config/profile.json`, which is **git-ignored** so it
never gets published. Create it from the template and fill in your data:

```bash
cp config/profile.example.json config/profile.json
# then edit config/profile.json (name, phone, employer, income, household, …)
```

Every generated German application uses these values. If the file is missing, the
app falls back to harmless placeholder text.

### 3. Initialize the database

```bash
python src/database.py              # creates data/kassel.db
python src/seed.py                  # (optional) seed your existing tracked applications
```

### 4. Start the backend + dashboard

```bash
python src/api.py
```

Open the dashboard at **http://localhost:8000/dashboard/**

### 5. Install the Chrome extension

1. Open `chrome://extensions` in Chrome.
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked** and select the `extension/` folder.
4. Click the extension icon → **Connect** (it should say
   *"Connected ✓ — N listings"*, confirming it reached the backend).

### 6. Capture listings

- Click the extension's **Search new properties on ImmoScout24** — it opens the
  pre-filtered search (3+ rooms, ≤ €1,100 warm, floors 2–3, 5 km around Kassel).
- When results load, the green **Kassel Agent** bar (bottom-right) captures them
  automatically and sends them to your dashboard.
- Refresh the dashboard to see them, with drafts queued under **Needs your input**.

### 7. Apply (you stay in control)

- Review / edit a draft on the dashboard and click **Approve**.
- On the listing's ImmoScout24 contact form, click the extension bar's
  **Insert my application** → it fills the German message → **you review and click
  ImmoScout's own Send button.**

---

## Optional: Gmail reply tracking

1. Google Cloud Console → new project → enable **Gmail API**.
2. Create **OAuth 2.0 Client ID** (type: *Desktop app*), download JSON as
   `config/credentials.json`.
3. Add your address as an OAuth test user.
4. First run opens a browser to authorize:
   ```bash
   python src/gmail_checker.py
   ```
5. Afterwards, the dashboard's **Sync Gmail** button reads new landlord replies
   (read-only — it never sends mail) and queues ones needing your input.

## Optional: Playwright scraper / cron

`src/scraper_immoscout.py`, `src/login_immoscout.py`, and `src/cron_runner.py` provide
a headless-browser alternative to the extension. Note that ImmoScout24 actively blocks
automated browsers (CAPTCHA / login walls), which is exactly why the **extension**
(your real session) is the recommended path. If you use the scraper:

```bash
pip install playwright && playwright install chromium
python src/login_immoscout.py       # one-time manual login, saves session
python src/scraper_immoscout.py --dry
```

---

## Editing your applicant profile

Open `src/application_template.py` and edit the `APPLICANT` dictionary (name, phone,
employer, income, household, etc.). Every generated German application uses these
values. Keep this file — and the whole repo — private.

## Notes & limitations

- **Floor data** comes from each listing's detail page (the search cards don't show
  it). The dashboard flags 4th-floor+ in red and top-floor (Dachgeschoss) in orange.
- **Map pins** need coordinates; captured listings currently store district but not
  exact lat/long, so they may not all appear as pins yet.
- **ImmoScout24 Terms:** this tool is for your own personal search. Keep captures at a
  human pace; don't mass-automate. Sending stays manual by design.
