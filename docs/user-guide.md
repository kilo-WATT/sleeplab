# SleepLab User Guide

Welcome to the SleepLab User Guide. This comprehensive guide helps you understand, configure, and operate SleepLab to monitor and analyze your sleep therapy data.

---

## 1. Core Concepts

SleepLab is a local-first, privacy-focused health data platform designed to process, store, and visualize CPAP therapy metrics alongside optional wearable telemetry. All data remains in your control, stored in your local PostgreSQL database.

### Data Ingestion Pipeline

SleepLab supports three main ingestion paths:
1. **Manual SD Card Uploads:** Select your SD-card root (containing `STR.edf` and `DATALOG`) in the web interface; the default cpap-parser backend inspects and imports it.
2. **SleepHQ Cloud Sync:** Import historical records directly via the SleepHQ API.
3. **Automated Webhooks:** Trigger automated imports when syncing via hardware uploaders like the ESP32-based `CPAP_data_uploader`.

---

## 2. CPAP SD Card Ingestion

In SleepLab 2.0, ResMed SD cards are imported through the **cpap-parser** backend
by default. You select the **whole SD-card root** — the folder that contains both
`STR.edf` and the `DATALOG` directory — not the `DATALOG` folder alone. SleepLab
inspects the full source, plans the import, and then runs it.

### Importing Data via Web Interface

Follow these steps to import your CPAP SD card data:

1. Insert your CPAP machine's SD card into your computer.
2. Log in to the SleepLab web interface.
3. Navigate to the **Import** screen.
4. Select the **SD-card root folder** (the one containing `STR.edf` and
   `DATALOG`). SleepLab first inspects the source and shows what it detected.
5. Confirm to start the import. The frontend uploads the files in batches and the
   backend imports them in the background, reporting honest stages (scan, parser
   selection, session/event writes, waveform chunks, finalizing).

> [!NOTE]
> Large historical cards may take several minutes. The completion card and
> **Import History** show which importer ran, sessions added or already present,
> events, waveform chunks, and any warnings or errors. Re-importing the same card
> is an idempotent no-op.

### Legacy / native fallback

The older native importer is retained as an explicit fallback — for example to
keep extending a machine whose history was first created by that backend. Set
`SLEEPLAB_USE_CPAP_PARSER=0` and restart SleepLab to route ResMed imports through
it. SleepLab never rewrites existing sessions or mixes the two backends for one
machine; see the parser import-troubleshooting notes in the README for the 503
(parser unavailable) and 409 (mixed history / DATALOG-in-parser-mode) responses.

### Direct CLI Import (legacy / native)

With direct access to the host or container you can run the native importer
manually. This uses the legacy DATALOG path and is the same backend as the
fallback above:

```bash
cd importer
python3 import_sessions.py --datalog /absolute/path/to/DATALOG --user-id <user-uuid>
```

#### CLI Options:

- **Filter by specific folder date:**
  ```bash
  python3 import_sessions.py --datalog /path/to/DATALOG --user-id <uuid> --folder 20241215
  ```
- **Filter from a start date:**
  ```bash
  python3 import_sessions.py --datalog /path/to/DATALOG --user-id <uuid> --from 20250101
  ```

---

## 3. SleepHQ Integration

If you store your CPAP logs on SleepHQ, you can sync your sessions directly into SleepLab without manually handling SD cards.

### Prerequisites

You need your SleepHQ OAuth credentials and your Team ID. Obtain these credentials from your SleepHQ developer portal or account settings.

### Configuration

Add your SleepHQ integration credentials in the web UI under **Settings → SleepHQ Integration** or specify them in your `.env` configuration file:

```env
SLEEPHQ_CLIENT_ID=your-client-id
SLEEPHQ_CLIENT_SECRET=your-client-secret
SLEEPHQ_TEAM_ID=your-team-id
```

### Syncing Sessions

1. Open the **Import → Sync from SleepHQ** screen in the dashboard.
2. Click **Sync Now** to start fetching the last 30 days of data.
3. To sync a specific date range, use the CLI importer:
   ```bash
   cd importer
   python3 sleephq_import.py --user-id <user-uuid> --from 2024-01-01 --to 2025-01-01
   ```

> [!TIP]
> Sessions imported from SleepHQ use a unique identifier format `sleephq-{record_id}` to prevent collisions with direct SD card imports.

---

## 4. Timezone Management

Understanding how SleepLab handles timezones is critical for displaying accurate charts and aligning CPAP data with wearable data.

### Configuration Variables

Configure two distinct IANA timezone settings in your `.env` file:

| Environment Variable | Default | Purpose |
|---|---|---|
| `MACHINE_TZ` | `UTC` | The timezone configured on your CPAP machine's internal clock. The importer uses this to convert naive timestamps into UTC before database storage. |
| `DISPLAY_TZ` | `UTC` | The timezone used to render all timeline labels, plot axes, and session durations on the dashboard. |

### Example Setup

If you live in New York and your CPAP clock is set to local time, specify:

```env
MACHINE_TZ=America/New_York
DISPLAY_TZ=America/New_York
```

> [!WARNING]
> Changing `MACHINE_TZ` only affects new imports. To fix existing database sessions imported with the wrong timezone, re-run the importer with the `--from` flag to reparse and overwrite them.

---

## 5. AI-Generated Summaries

SleepLab uses large language models (LLMs) to automatically generate plain-text clinical summaries of your daily sessions and long-term sleep trends.

### Supported Providers

Configure your preferred AI provider in `.env` using these environment variables:

#### 1. OpenAI (Cloud-hosted)
```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your-api-key
OPENAI_MODEL=gpt-4o
```

#### 2. Local Ollama (Self-hosted)
```env
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=llama3.1:8b
```

#### 3. LiteLLM Proxy
```env
LLM_PROVIDER=litellm
LITELLM_BASE_URL=http://localhost:4000/v1
LITELLM_MODEL=gpt-4o-mini
```

#### 4. Custom OpenAI-compatible API
```env
LLM_PROVIDER=custom
LLM_BASE_URL=https://api.yourprovider.com/v1
LLM_API_KEY=your-custom-api-key
LLM_MODEL=your-chosen-model
```

### Health Check

Verify your LLM configuration and connection status using the following API endpoint:

```bash
curl -X GET http://localhost:8000/llm/health
```

---

## 6. Troubleshooting

### Connection and Authentication
- **Problem:** Frontend loads but displays API connection errors.
  - **Solution:** Verify `API_URL` and `CORS_ALLOWED_ORIGINS` in your environment. If the frontend is hosted on a different port or domain, `CORS_ALLOWED_ORIGINS` must include the frontend's origin URL (e.g., `http://localhost:8080`).
- **Problem:** HTTP 403 Forbidden on import webhooks.
  - **Solution:** Verify that the `X-Import-Secret` header in your uploader configuration matches `IMPORT_WEBHOOK_SECRET` in your SleepLab `.env` file.

### Ingestion Issues
- **Problem:** Webhook returns HTTP 400 "path not found".
  - **Solution:** The background importer cannot find the DATALOG folder. Confirm that your Docker volumes are mounted correctly. The NAS share directory must be bind-mounted into the SleepLab container at `/data` as read-only.
- **Problem:** Import finishes successfully but no new sessions appear.
  - **Solution:** With the cpap-parser backend, re-importing the same card
    snapshot is an idempotent no-op — already-imported nights report as
    `unchanged` rather than duplicating. If you expected _new_ nights, confirm the
    card actually contains later dates. To rebuild a machine from scratch, delete
    its sessions first, then re-import.

### CPAP Import (cpap-parser backend)

- **Verify the active backend.** Open `GET /config` (e.g.
  `http://localhost:8000/config`). `resmed_import_backend` should be
  `cpap-parser` and `resmed_import_ready` should be `true`.
- **HTTP 503 at the end of an import** means the parser backend is selected but
  its runtime isn't installed. Use the published Docker image (it bundles the
  parser) or run `uv sync --extra parser --group dev`, then restart SleepLab.
- **HTTP 409 on import** means this machine already has history from the _other_
  backend. SleepLab won't mix them and leaves existing data untouched. Set
  `SLEEPLAB_USE_CPAP_PARSER=0` and restart to continue on the legacy/native
  fallback, or delete the machine's sessions to start fresh on the parser.
- **HTTP 409 on a DATALOG/webhook import** is expected while the parser backend
  is selected: those legacy flows are disabled. Import the full card **root**
  through the standard Import screen, or switch to the fallback.
- **A `resmed_summary_only_day` warning** is not an error — that day had only
  ResMed summary data (no detailed `DATALOG`), so usage/AHI import but there are
  no waveforms or scored events to inspect for it.

See the README's "Import troubleshooting (ResMed / cpap-parser)" section for the
full error text of each response.
