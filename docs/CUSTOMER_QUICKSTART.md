# Axion — Customer quick start

A 5-minute walkthrough from first launch to "I'm using it."

## 1. Install Python (one time)

Axion needs **Python 3.11 or newer**.

- **macOS:** Install via Homebrew (`brew install python@3.12`) or from <https://www.python.org/downloads/>.
- **Windows:** Download from <https://www.python.org/downloads/> and **tick "Add Python to PATH"** during install.

Check it works:

```bash
python3 --version    # macOS / Linux
python --version     # Windows
```

You should see `Python 3.11.x` or higher.

## 2. Launch Axion

**macOS / Linux:**
```bash
./scripts/run_local.sh
```

**Windows:**
```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
```

Or simply **double-click `Axion.bat`** on Windows.

The first run takes 1–2 minutes (venv + dependencies). Subsequent runs are fast.

When you see:

```
============================================
  Axion is running.
============================================
  Dashboard : http://127.0.0.1:7777
```

…the dashboard will open in your browser automatically (macOS / Windows). If not, open <http://127.0.0.1:7777/dashboard/> manually.

## 3. Import a portfolio

In the dashboard:

1. Click **Portfolio** → **Holdings**.
2. Drag in your CSV, or click **Upload** and pick `sample_portfolio.csv` from the project root to try the bundled demo data.
3. Review the extracted rows. Adjust any field if needed.
4. Click **Import**.

Your holdings appear in the table within a second.

### Supported CSV format

The simplest format is:

```
ticker,quantity,price,currency
AAPL,150,178.50,USD
MSFT,80,425.00,USD
NESN,40,98.50,CHF
```

Optional columns: `isin`, `name`, `avg_cost_basis`, `market_value`, `weight_pct`.

Tickers must be 1–10 characters (letters, digits, and `.`). Currency must be a 3-letter ISO code. Quantity must be > 0.

For PDFs and scanned documents, see "AI features" below.

## 4. Explore the dashboard

| Tab | Shows |
|-----|-------|
| **Portfolio** | Holdings, exposures (sector / geography / currency), trade history |
| **Insights** | Events collected from news sources, ranked by impact on your holdings |
| **Alerts** | Concentration breaches, calendar clusters, stale data warnings |
| **Assistant** | Conversational queries (requires AI provider — disabled otherwise) |
| **Settings** | API keys, provider selection, source health |

## 5. (Optional) Configure an AI provider

The core platform runs without AI. If you want LLM-enhanced impact scoring, narrative digests, and conversational queries:

1. Get an API key from Anthropic, OpenAI, or Google.
2. In the dashboard, go to **Settings → AI Provider**.
3. Select the provider, paste the key, click **Save**.
4. Restart Axion (Ctrl+C in the launcher window, then re-run the launcher).
5. **Settings → AI Provider → Test** confirms the key works.

Keys are stored at `~/.axion.env` with `600` permissions. They are never sent anywhere except to the chosen provider.

## 6. (Optional) Add news sources

Axion ships with a curated allowlist in `config/sources.yaml`. Some sources are public RSS feeds and work immediately (e.g. Federal Reserve press releases). Others need an API key.

Go to **Settings → Sources** to see which are healthy. Disabled sources display a clear reason.

## 7. Stopping and restarting

- **Stop:** Press **Ctrl+C** in the launcher window. The server shuts down cleanly.
- **Restart:** Re-run the launcher. Your data and settings persist in `~/axion-data/` and `~/.axion.env`.

## 8. Verifying the install

If anything looks off, run the bundled smoke test:

```bash
.venv/bin/python scripts/smoke_local.py   # macOS / Linux
.venv\Scripts\python.exe scripts\smoke_local.py   # Windows
```

It runs 16 end-to-end checks against a throwaway temp DB (your real data is untouched) and prints PASS / FAIL for each.

## 9. Sending Axion's state to support

If something doesn't work and you'd like to share state with us, run:

```bash
.venv/bin/python scripts/support_bundle.py        # macOS / Linux
.venv\Scripts\python.exe scripts\support_bundle.py  # Windows
```

This writes a redacted zip to `~/axion-data/support/`. Attach that single file — it carries the app version, OS info, schema version, table counts, source health summary, and the last 200 KB of each log file. Secrets are removed by both env-var name and value pattern. The zip never contains your database, backup files, raw `.env`, or holdings.

## 10. Port already in use?

If the launcher reports `Port 7777 is in use by another application`, it also tries to show the process name and PID. Two options:

- **Close the other application** (often a stale Axion from a previous launch).
- **Run Axion on a different port:**
  ```bash
  AXION_PORT=7778 ./scripts/run_local.sh                # macOS / Linux
  ```
  ```powershell
  $env:AXION_PORT='7778'; PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1   # Windows
  ```

The dashboard URL will be `http://127.0.0.1:7778/dashboard/` in that case.

## Where your data lives

| Item | Location |
|------|----------|
| Database, logs, exports | `~/axion-data/` (or `~/kleitos-data/` on older installs) |
| API keys and settings | `~/.axion.env` |
| Source allowlist | `config/sources.yaml` (in the project folder) |
| Pre-upgrade safety backups | `~/axion-data/backups/kleitos-pre-v<N>-<timestamp>.db` |

### What happens during an upgrade

When you launch a new build of Axion against an older database, the launcher:

1. Verifies the database is readable and not corrupt.
2. Creates a consistent backup at `~/axion-data/backups/kleitos-pre-v<N>-<timestamp>.db` (where `<N>` is the new schema version).
3. Only then applies the migration steps.

If the backup write fails, the launcher refuses to migrate and tells you what to fix. **Your live database is never modified.**

### If you see one of these recovery messages

- **"Your Axion data was created by a newer version of Axion"** — this build's schema is older than your data. Either update Axion, or restore an older backup from `~/axion-data/backups/`. Your data is intact.
- **"Axion could not open the database"** — the DB file is corrupt or unreadable. Axion does **not** delete or overwrite it. Restore a backup, or move the file aside and relaunch for a fresh DB.
- **"Pre-migration backup failed"** — free disk space or fix folder permissions on `~/axion-data/backups/`, then relaunch. No schema change was applied.

To start completely fresh, see [DEMO_RESET.md](DEMO_RESET.md).

## Need help?

- Architecture: [../ARCHITECTURE.md](../ARCHITECTURE.md)
- Honest limitations: [../KNOWN_LIMITATIONS.md](../KNOWN_LIMITATIONS.md)
- Troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Operator runbook: [../OPERATOR_CHECKLIST.md](../OPERATOR_CHECKLIST.md)
