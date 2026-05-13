# Axion — Local install

This is the **one** supported path to run Axion as a local application on a single machine. No Docker, no cloud, no admin rights.

## Requirements

- **Python 3.11 or newer** (3.12 recommended)
- **Internet access** for the first run (pip installs dependencies) and ongoing news collection
- About **300 MB of disk** for the venv + dependencies

## macOS / Linux

From the project root:

```bash
./scripts/run_local.sh
```

That's it. The first run takes 1–2 minutes (creating a venv and installing dependencies). After that, startup is a few seconds.

When the server is healthy, the dashboard opens automatically (macOS). On Linux, open <http://127.0.0.1:7777/dashboard/> manually.

Stop with **Ctrl+C** in the terminal.

> **macOS users — which path do I pick?** This repo also ships `Axion.app` for Finder users. Both work and both share `~/axion-data` for data. The **terminal launcher (`scripts/run_local.sh`) is the supported customer path** — it's verified by the smoke test and has the smaller blast radius (no `/Applications` install, no launchd auto-start, no code-signing gymnastics). `Axion.app` is an extra option for users who prefer double-clicking from Finder, but it is **not code-signed** so first launch needs right-click → Open to clear Gatekeeper.

## Windows

From a PowerShell window in the project root:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
```

Or double-click **`Axion.bat`** in the project root for a guided launch (it ends up running the same flow plus a tray app if its dependencies are installed).

When the server is healthy, the dashboard opens automatically.

Stop with **Ctrl+C** in the PowerShell window.

## What it does

1. Verifies Python 3.11+.
2. Creates `.venv/` in the project root if missing.
3. Installs `requirements.txt` into the venv.
4. Creates `~/axion-data/` (or `~/kleitos-data/` if it already exists, for backward compatibility).
5. Runs database migrations against `~/axion-data/db/kleitos.db`.
6. Starts FastAPI/uvicorn on **`127.0.0.1:7777`** (loopback only — never exposed to the network).
7. Opens the dashboard in your browser.

## Common options

| Want to… | How |
|----------|-----|
| Use a different port | `AXION_PORT=7778 ./scripts/run_local.sh` |
| Use a different data dir | `AXION_DATA_DIR=/tmp/axion-test ./scripts/run_local.sh` |
| Stop the server | Ctrl+C in the launcher window |
| Reset to a clean state | See [docs/DEMO_RESET.md](docs/DEMO_RESET.md) |

## After it's running

- **Dashboard:** <http://127.0.0.1:7777/dashboard/>
- **API docs:** <http://127.0.0.1:7777/docs>
- **Health:** <http://127.0.0.1:7777/api/v1/health>

To import a portfolio, use the Portfolio tab in the dashboard or upload `sample_portfolio.csv` from the project root for a quick start.

## AI features (optional)

Axion's core (portfolio management, exposures, alerts, source collection, deterministic risk rules) runs without any AI provider.

If you add an Anthropic / OpenAI / Google API key in the dashboard's **Settings → AI Provider** screen, you also get:

- LLM-enhanced impact scoring
- Daily narrative digests
- Conversational assistant tab
- AI vision extraction for scanned-PDF portfolio imports

Keys are stored at `~/.axion.env` with `600` permissions. Without a key, the related UI surfaces clearly indicate "disabled" — no fake output.

## Data, backups, and upgrades

| Item | Location |
|------|----------|
| Database, logs, exports | `~/axion-data/` (or legacy `~/kleitos-data/`) |
| API keys and settings | `~/.axion.env` |
| Source allowlist | `config/sources.yaml` (in the project folder) |
| Pre-upgrade safety backups | `~/axion-data/backups/kleitos-pre-v<N>-<timestamp>.db` |

When you launch a newer Axion build against an older database, the launcher **automatically creates a safety backup before applying any schema change** — same data dir, `backups/` sub-folder, named `kleitos-pre-v<schema-version>-<YYYYMMDD-HHMMSS>.db`. The backup is a consistent snapshot via SQLite's `Connection.backup()`, not a raw file copy, so it's safe to use directly.

If the backup write fails (disk full, permissions), the launcher refuses to migrate and tells you why. Your live database is left untouched.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Python 3.11 or newer is required` | Install from <https://www.python.org/downloads/> and re-run. On Windows, tick **Add Python to PATH**. |
| `Port 7777 is in use` | Either stop the other app, or `AXION_PORT=7778 ./scripts/run_local.sh`. |
| Dashboard shows "degraded" | Open <http://127.0.0.1:7777/api/v1/health> for details — usually means the scheduler hasn't completed its first cycle yet (give it a minute). |
| **"Your Axion data was created by a newer version"** | This build's schema is older than your DB. Update Axion, or restore an older backup from `~/axion-data/backups/`. Your data is unchanged. |
| **"Axion could not open the database"** | DB file is corrupt or unreadable. Axion does **not** delete or overwrite it. Restore a backup, or move the file aside and relaunch for a fresh DB. |
| **"Pre-migration backup failed"** | Free disk space or fix folder permissions on `~/axion-data/backups/`, then relaunch. No schema change was applied. |
| Want a totally fresh start | See [docs/DEMO_RESET.md](docs/DEMO_RESET.md). |
| Want to verify the install is correct | `python scripts/smoke_local.py` runs 16 end-to-end checks against a temp DB. |
| Want a programmatic recovery check while the server is up | `curl http://127.0.0.1:7777/api/v1/system/recovery` returns structured JSON about the DB state. |

## What this is NOT

- **Not** a cloud / multi-tenant service. Loopback-only, single user, single machine.
- **Not** a real-time market data terminal. News + events + portfolio analytics, on a 30-minute collection cycle.
- **Not** a substitute for a broker. No order routing.

See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) for the full honest list.
