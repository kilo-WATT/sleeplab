# SleepLab

[![GNU GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE.md)
[![Frontend](https://img.shields.io/github/package-json/v/joshuamyers-dev/sleeplab?label=frontend&color=informational)](frontend/package.json)
[![Backend](https://img.shields.io/github/pyproject/v/joshuamyers-dev/sleeplab?label=backend&color=informational)](pyproject.toml)

SleepLab is a local-first sleep therapy dashboard for importing and exploring ResMed CPAP data. It includes:

- A React + Vite frontend in `frontend/`
- A FastAPI backend in `api/`
- A PostgreSQL-backed importer in `importer/` for ResMed `DATALOG` folders

## SleepLab 2.0 beta

This branch (`develop/2.0`) is the SleepLab 2.0 beta line, currently released as
**v2.0.0-beta.1**. ResMed SD-card imports now run through the `cpap-parser`
backend by default, with the older native importer retained as an explicit
fallback. See the
[beta.1 release notes](docs/sleeplab_2_beta_1_release_notes.md) for what changed,
[known limitations](docs/sleeplab_2_beta_1_release_notes.md#known-limitations),
and upgrade/import cautions.

> **Your CPAP data is personal health data.** SleepLab is local-first and stores
> everything in your own PostgreSQL database. Keep your instance private — don't
> expose it to the public internet without authentication and a tightened
> `CORS_ALLOWED_ORIGINS`, and never attach real card files, serial numbers,
> dates, or session exports to public bug reports.

**Reporting beta feedback:** open an issue at
<https://github.com/kilo-WATT/sleeplab/issues> with your platform, the importer
backend (`cpap-parser` or legacy), what `GET /config` reports, and the relevant
`docker compose logs app` excerpt. Redact private details first.

## Screenshots

![SleepLab dashboard screenshot 1](https://sleeplab-static.s3.ap-southeast-2.amazonaws.com/screenshot-1.png)
![SleepLab dashboard screenshot 2](https://sleeplab-static.s3.ap-southeast-2.amazonaws.com/screenshot-2.png)
![SleepLab dashboard screenshot 3](https://sleeplab-static.s3.ap-southeast-2.amazonaws.com/screenshot-3.png)

## Stack

- Frontend: React 19, Vite, TypeScript, Tailwind
- Backend: FastAPI, SQLAlchemy, Uvicorn
- Database: PostgreSQL 16
- Workspace tooling: Nx

## Requirements

- Node.js 20+
- npm
- Python 3.12
- PostgreSQL 16

## Self-Hosting With Docker Compose

SleepLab can run as a self-hosted Docker stack with:

- PostgreSQL
- FastAPI backend
- Nginx-served frontend
- automatic schema migrations at API startup
- a prebuilt Docker image, so no local image build is required

Key files:

- [`compose.yaml`](compose.yaml)
- [`docker/entrypoint.sh`](docker/entrypoint.sh)
- [`docker/nginx.conf`](docker/nginx.conf)
- [`.env.example`](.env.example)

The default self-hosted image is:

```text
joshuaaaronmyers/sleeplab:latest
```

### Required Configuration

Create an env file for deployment by copying [`.env.example`](.env.example).

Set at minimum:

- `SECRET_KEY`

Optional but commonly needed:

- `OPENAI_API_KEY`
- `CORS_ALLOWED_ORIGINS`
- `API_URL`

Recommended values for a local/self-hosted machine:

```env
SECRET_KEY=replace-me-with-a-long-random-secret
OPENAI_API_KEY=
CORS_ALLOWED_ORIGINS=*
API_URL=http://localhost:8000
```

The self-hosted compose stack always uses the internal Postgres DSN:

```text
postgresql+psycopg2://cpap:cpap@postgres:5432/cpap
```

For the default self-hosted setup, `CORS_ALLOWED_ORIGINS` is `*` so the frontend can talk to the API regardless of whether you access it via `localhost`, `127.0.0.1`, or a LAN hostname/IP. If you expose the app publicly, tighten that value to your actual frontend origin(s).

### Start The Stack

```bash
docker compose up -d
```

If you want the newest published image first:

```bash
docker compose pull
docker compose up -d
```

### View Logs

```bash
docker compose logs -f
```

### Stop The Stack

```bash
docker compose down
```

### Copy-Paste Compose File

If you want to self-host quickly on a server, you can save this as `compose.yaml` and use it directly:

```yaml
services:
  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_DB: cpap
      POSTGRES_USER: cpap
      POSTGRES_PASSWORD: cpap
    volumes:
      - sleeplab_postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cpap -d cpap"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    image: joshuaaaronmyers/sleeplab:latest
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+psycopg2://cpap:cpap@postgres:5432/cpap
      SECRET_KEY: replace-me-with-a-long-random-secret
      OPENAI_API_KEY: ""
      CORS_ALLOWED_ORIGINS: "*"
      API_URL: http://localhost:8000
      API_HOST: 0.0.0.0
      API_PORT: 8000
    ports:
      - "8080:8080"
      - "8000:8000"

volumes:
  sleeplab_postgres_data:
```

Then start it with:

```bash
docker compose up -d
```

### `docker run` Command

If you already have PostgreSQL running separately, you can run just the SleepLab app container:

```bash
docker run -d \
  --name sleeplab \
  --restart unless-stopped \
  -p 8080:8080 \
  -p 8000:8000 \
  -e DATABASE_URL="postgresql+psycopg2://USER:PASSWORD@HOST:5432/cpap" \
  -e SECRET_KEY="replace-me-with-a-long-random-secret" \
  -e OPENAI_API_KEY="" \
  -e CORS_ALLOWED_ORIGINS="*" \
  -e API_URL="http://localhost:8000" \
  joshuaaaronmyers/sleeplab:latest
```

Notes:

- `docker run` does not include PostgreSQL. You must provide your own database.
- `API_URL` should be the URL the browser will use to reach the API.
- If the app is exposed publicly, replace `CORS_ALLOWED_ORIGINS="*"` with your real frontend origin(s).

### Default Self-Hosted URLs

- Frontend: `http://localhost:8080`
- API: `http://localhost:8000`

### What The Compose File Does

- starts PostgreSQL with a named volume
- pulls `joshuaaaronmyers/sleeplab:latest`
- exposes the frontend on `8080`
- exposes the API on `8000`
- waits for Postgres to become healthy
- runs migrations automatically at API startup

### Persistence

Database data is stored in the named volume:

- `sleeplab_postgres_data`

### Upgrade Workflow

```bash
git pull
docker compose pull
docker compose up -d
```

Migrations run automatically through [`server.py`](server.py) when the API starts.

### Troubleshooting

- If Docker Compose says the image is missing, run `docker login` and `docker compose pull`.
- If the frontend loads but API requests fail, verify `API_URL` and `CORS_ALLOWED_ORIGINS`.
- If the API container exits early, inspect `docker compose logs app` for DB or migration errors.
- If AI summaries are unavailable, check `GET /llm/health` for provider status and confirm the relevant env vars are set (see **AI Summaries** below).
- If you are deploying to a Linux server, use the published multi-arch image tag rather than an old locally built arm-only image.

## Quick Start

### 1. Install dependencies

```bash
npm install
cd frontend && npm install
```

### 2. Start Postgres

The repo includes a local Postgres service inside Docker Compose:

```bash
docker compose up -d postgres
```

Default database settings from [`compose.yaml`](compose.yaml):

- Database: `cpap`
- Username: `cpap`
- Password: `cpap`
- Port: `5432`

The API currently connects to:

```python
postgresql+psycopg2://localhost/cpap
```

That is defined in [`api/database.py`](api/database.py). If your local database setup differs, update that file or add your own configuration layer.

### 3. Apply schema migrations

Run the SQL files in `migrations/` against the `cpap` database in order:

```bash
psql -d cpap -f migrations/001_add_auth.sql
psql -d cpap -f migrations/002_scope_sessions_per_user.sql
psql -d cpap -f migrations/003_add_public_ids.sql
psql -d cpap -f migrations/004_reset_uuid_ids.sql
psql -d cpap -f migrations/005_add_user_profile_fields.sql
```

### 4. Run the app

Start frontend and backend together:

```bash
npm run dev
```

Or run them separately:

```bash
npm run api
npm run frontend
```

Default local URLs:

- Frontend: `http://127.0.0.1:5173`
- API: `http://127.0.0.1:8000`

## Development Commands

Run these from the repository root:

| Command | Checks |
|---|---|
| `npm run build` | Builds the frontend with TypeScript and Vite through Nx. |
| `npm run lint` | Runs the frontend ESLint check through Nx. |
| `npm run test` | Runs the existing backend and frontend test targets through Nx. |
| `npm run typecheck` | Runs the frontend TypeScript check through Nx. |
| `npm run test:frontend` | Runs only the frontend Vitest suite through Nx. |

Run these from the root with npm workspace targeting:

| Command | Checks |
|---|---|
| `npm run build -w frontend` | Builds the frontend directly from the frontend workspace. |
| `npm run lint -w frontend` | Runs the frontend ESLint check directly. |
| `npm run test -w frontend` | Runs the frontend Vitest suite directly. |
| `npm run typecheck -w frontend` | Runs the frontend TypeScript check directly. |

## Timezones

Two IANA timezone settings control how session data is interpreted and displayed.

| Variable | Default | Purpose |
|---|---|---|
| `MACHINE_TZ` | `UTC` | The timezone your CPAP machine is set to. The importer uses this to correctly interpret the naive local timestamps embedded in EDF files before storing them as UTC in the database. |
| `DISPLAY_TZ` | `UTC` | The timezone used to format all time labels in the UI — plot axes, event timeline, session start time. Set this to your local timezone for accurate display. |

Both values must be valid [IANA timezone names](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (e.g. `America/New_York`, `Europe/London`, `Australia/Sydney`).

Set them in your `.env` file or `compose.yaml`:

```env
MACHINE_TZ=America/New_York
DISPLAY_TZ=America/New_York
```

If your machine is set to the same timezone as your display, both values will be identical. If you travel with your CPAP and don't update the machine clock, set `MACHINE_TZ` to the machine's home timezone and `DISPLAY_TZ` to wherever you want times displayed.

> **Re-importing after changing `MACHINE_TZ`:** The importer attaches the timezone at import time. If you change `MACHINE_TZ` after sessions are already in the database, re-run the importer with `--from` to update affected sessions.

## Auth

SleepLab uses bearer-token auth.

- `POST /auth/register` and `POST /auth/login` return `{ token, user }`
- The frontend stores the JWT in browser `localStorage`
- Authenticated API requests send `Authorization: Bearer <token>`

Relevant files:

- [`api/auth.py`](api/auth.py)
- [`api/routers/auth.py`](api/routers/auth.py)
- [`frontend/src/api/client.ts`](frontend/src/api/client.ts)
- [`frontend/src/context/AuthContext.tsx`](frontend/src/context/AuthContext.tsx)

## Importing Data

SleepLab imports ResMed SD card data through the recommended SleepLab 2.0
`cpap-parser` path by default. Select the SD card root (the folder containing
`STR.edf` and `DATALOG`) so SleepLab can inspect and import the complete source.

In the UI:

1. Create an account or log in.
2. Open the import screen.
3. Select the SD card root folder.
4. The frontend uploads the files in batches to the API.
5. The API runs the importer in the background. The UI reports honest stages
   (scan, parser selection, session/event writes, waveform chunks, finalizing)
   rather than estimating a percentage it cannot know.
6. The completion card and Import History show the importer used, sessions
   added or already present, events, waveform chunks, warnings, and errors when
   those values were recorded. Older history remains readable.

The upload/import endpoints are implemented in `api/routers/upload.py`. The
default parser loader lives under `importer/loaders/`; the older native importer
in `importer/import_sessions.py` remains available as an explicit fallback.

To select the legacy/native fallback, set `SLEEPLAB_USE_CPAP_PARSER=0` and
restart SleepLab. This is useful for continuing a machine history originally
created by that backend. SleepLab does not rewrite existing sessions or mix the
two backends for one machine.

Re-importing the same card snapshot is an idempotent no-op. A changed snapshot
imports only new work where possible. If SleepLab blocks mixed parser/native
history, it leaves existing sessions untouched and explains how to continue
with the matching importer.

You can also run the legacy/native importer manually:

```bash
cd importer
python3 import_sessions.py --datalog /absolute/path/to/DATALOG --user-id <user-uuid>
```

Optional filters:

```bash
python3 import_sessions.py --datalog /absolute/path/to/DATALOG --user-id <user-uuid> --folder 20241215
python3 import_sessions.py --datalog /absolute/path/to/DATALOG --user-id <user-uuid> --from 20250101
```

## SleepHQ Import

Sessions can be pulled directly from [SleepHQ](https://sleephq.com) without an SD card.

### Setup

Add your SleepHQ OAuth credentials and team ID in **Settings → SleepHQ Integration**, or set them in `.env`:

```env
SLEEPHQ_CLIENT_ID=your-client-id
SLEEPHQ_CLIENT_SECRET=your-client-secret
SLEEPHQ_TEAM_ID=your-team-id   # optional — auto-resolved if omitted
```

OAuth credentials are available from your SleepHQ developer/account settings.

### Sync from the UI

Open **Import → Sync from SleepHQ** and click **Sync now**. The last 30 days of sessions are fetched and written to the database. Sessions imported this way use a `sleephq-{id}` session ID to avoid collisions with SD card imports.

### CLI

```bash
cd importer
python3 sleephq_import.py --user-id <user-uuid> --days 30

# Explicit date range
python3 sleephq_import.py --user-id <user-uuid> --from 2024-01-01 --to 2025-01-01

# Dry run — fetch and map without writing to the database
python3 sleephq_import.py --user-id <user-uuid> --days 30 --dry-run
```

The importer retries automatically on rate-limit (HTTP 429) responses and pauses between paginated requests, so long historical back-fills work without manual intervention.

## AI Summaries

AI-generated session and trend summaries are powered by any OpenAI-compatible LLM backend. The provider is selected automatically based on environment variables — existing deployments with `OPENAI_API_KEY` continue to work with no changes.

### Provider detection

| `LLM_PROVIDER` | Backend | Required env vars |
|---|---|---|
| `openai` / auto-detected | OpenAI cloud | `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-4o`) |
| `ollama` / default when no key | Local Ollama | `OLLAMA_BASE_URL` (default `http://localhost:11434/v1`), `OLLAMA_MODEL` (default `llama3.1:8b`) |
| `litellm` | LiteLLM proxy | `LITELLM_BASE_URL` (default `http://localhost:4000/v1`), `LITELLM_MODEL` (default `gpt-4o-mini`) |
| `custom` | Any OpenAI-compatible endpoint | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |

### Health check

```
GET /llm/health
```

Returns the active provider, base URL, model, and whether the backend is reachable.

### Self-hosted with Ollama

```yaml
# compose.yaml
services:
  app:
    environment:
      LLM_PROVIDER: ollama
      OLLAMA_BASE_URL: http://ollama:11434/v1
      OLLAMA_MODEL: llama3.1:8b
  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    volumes:
      - ollama_data:/root/.ollama
```

Without any LLM configuration, core dashboard features still work but AI summary endpoints return an error message instead of generated output.

## Project Layout

```text
api/         FastAPI application
frontend/    React/Vite client
importer/    ResMed EDF parsing and import pipeline
migrations/  SQL migrations
```

## Contributing

For local development:

1. Install dependencies:

```bash
npm install
cd frontend && npm install
```

2. Start Postgres:

```bash
docker compose up -d postgres
```

3. Run the app:

```bash
npm run dev
```

Useful commands:

```bash
npm run api
npm run frontend
npm run build
npm run lint
npm run test
```

Before opening a PR, make sure:

- the frontend builds successfully
- lint passes for the frontend
- any README or env changes are documented
- self-hosting changes are reflected in [`compose.yaml`](compose.yaml) and [`.env.example`](.env.example) where relevant

## Notes

- The backend reads `DATABASE_URL` from environment and falls back to a local development default in [`api/database.py`](api/database.py).
- The backend uses a fallback development JWT secret if `SECRET_KEY` is not set. Set a real `SECRET_KEY` outside local development.

## Acknowledgements

This project depends on **open-cpap-parser** as a key functional module for
multi-manufacturer CPAP data parsing. All SleepyHead/OSCAR-derived binary
parsing code lives in open-cpap-parser — SleepLab itself does not implement
any direct derivative of SleepyHead or OSCAR.

open-cpap-parser is a derivative of the free and open-source software
**SleepyHead**, developed and copyright by Mark Watkins (Jedimark) (C) 2011-2018,
and of **[OSCAR](https://gitlab.com/CrimsonNape/OSCAR-code)** (Open Source CPAP
Analysis Reporter), which is itself a derivative of SleepyHead. The binary-format
parsing logic in open-cpap-parser's Rust extension module is ported from OSCAR.

Per Mark Watkins' redistribution request, any derivative of this work
must mention clearly in its advertising material, software installer, and
about screens that it **"is based on the free and open-source software
SleepyHead, developed and copyright by Mark Watkins (C) 2011-2018."**
Referencing "GPL software" alone is not sufficient. See [NOTICE.md](NOTICE.md)
for the full redistribution notice and third-party copyright statements.

## License

GNU General Public License v3.0 (GPL-3.0)

This project is licensed under the GNU General Public License v3.0.
See [LICENSE.md](LICENSE.md) for the full license text.

This project incorporates **open-cpap-parser** as a key functional module,
which is a derivative of **[OSCAR](https://gitlab.com/CrimsonNape/OSCAR-code)**
and the free and open-source software **SleepyHead**, developed and copyright by
Mark Watkins (Jedimark) (C) 2011-2018. Both SleepyHead and OSCAR are distributed
under the GPL-3.0, which this project inherits. All other components are
permissively licensed (MIT, BSD-3-Clause, Apache-2.0) and are compatible with
GPL-3.0.
