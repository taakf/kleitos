# Axion — Reset and recovery

Use this guide to return Axion to a clean state, recover from corrupted data, or prepare a fresh demo.

## What "reset" means

There are three things you can reset independently:

| Item | Where it lives | Effect of deleting |
|------|----------------|--------------------|
| **Data** (DB, logs, exports) | `~/axion-data/` | Empty portfolio, no events, no alerts, no history |
| **Settings & keys** | `~/.axion.env` | Removes saved API keys and provider preferences |
| **Code dependencies** | `<project>/.venv/` | Forces a full reinstall on next launch (slower) |

The **project folder itself** (`src/`, `dashboard/`, `config/`) is never touched by a reset — it's the application, not your data.

## 1. Full reset (the most common case)

Use this for a fresh demo or to recover from any state.

### Stop Axion first

In the launcher window, press **Ctrl+C**. Wait for the prompt to return.

### macOS / Linux

```bash
# Delete data (DB, logs, backups, exports)
rm -rf ~/axion-data
rm -rf ~/kleitos-data         # only if you have the legacy directory

# Delete saved settings and API keys
rm -f ~/.axion.env
rm -f ~/.kleitos.env          # legacy

# (Optional) wipe the venv to force a clean reinstall
rm -rf .venv
```

### Windows (PowerShell)

```powershell
# Delete data
Remove-Item -Recurse -Force "$env:USERPROFILE\axion-data"
Remove-Item -Recurse -Force "$env:USERPROFILE\kleitos-data" -ErrorAction SilentlyContinue

# Delete saved settings
Remove-Item -Force "$env:USERPROFILE\.axion.env" -ErrorAction SilentlyContinue
Remove-Item -Force "$env:USERPROFILE\.kleitos.env" -ErrorAction SilentlyContinue

# (Optional) wipe the venv
Remove-Item -Recurse -Force .venv
```

### Relaunch

```bash
./scripts/run_local.sh        # macOS / Linux
```
```powershell
.\scripts\run_local.ps1       # Windows
```

The launcher will recreate everything from scratch. First launch after a venv wipe takes 1–2 minutes.

## 2. Reset only the data (keep API keys)

```bash
rm -rf ~/axion-data
```

Relaunch. Database is rebuilt, default portfolio is reseeded, but your AI keys and provider choices remain.

## 3. Reset only the database (keep logs and exports)

```bash
rm -rf ~/axion-data/db
```

Or on Windows:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\axion-data\db"
```

The next launch runs migrations from a clean DB. The default portfolio is recreated. Logs from prior runs survive.

## 4. Reinstall dependencies (when something is "weird")

If pip installs partially failed, or you upgraded Python:

```bash
rm -rf .venv
./scripts/run_local.sh
```

The launcher recreates `.venv` and reinstalls everything in `requirements.txt`.

## 5. Verify the reset worked

```bash
.venv/bin/python scripts/smoke_local.py
```

The smoke test runs 16 end-to-end checks in an isolated temp DB. It should report `16/16 passed`. If anything fails, the report tells you exactly which check failed.

## 6. Restore from a backup

There are two kinds of backups in `~/axion-data/backups/`:

| Filename pattern | When it's written | What it contains |
|---|---|---|
| `kleitos-pre-v<N>-<YYYYMMDD-HHMMSS>.db` | **Automatically**, by the launcher, every time a schema upgrade is about to run. | A consistent snapshot of the live DB at the moment **before** schema v`<N>` was applied. |
| `kleitos-<YYYY-MM-DD>.db` | Optionally, by `scripts/backup.sh` (e.g. a cron job). | A scheduled snapshot of the live DB. |

To roll back to either kind:

1. Stop Axion (Ctrl+C in the launcher window).
2. Pick the backup you want, e.g. `~/axion-data/backups/kleitos-pre-v8-20260514-013500.db`.
3. Replace the live DB:
   ```bash
   cp ~/axion-data/backups/kleitos-pre-v8-20260514-013500.db ~/axion-data/db/kleitos.db
   ```
4. Relaunch.

The migration system will leave the schema as-is if the backup is already at head; otherwise it will upgrade in place (creating a fresh `kleitos-pre-v…-…` backup of *that* version first).

## 6a. Recovery messages from the launcher

The launcher prints clean, copy-pasteable messages for the three failure modes. None of them modifies your data file.

- **"Your Axion data was created by a newer version of Axion"** (exit code 2) — your DB is at a higher schema version than this build supports. Update Axion or restore an older `kleitos-pre-v<N>-*.db` backup.
- **"Axion could not open the database"** (exit code 3) — DB file is corrupt or unreadable. Restore a backup, or move the file aside and relaunch for a fresh DB.
- **"Pre-migration backup failed"** (exit code 4) — disk full or permissions; free space / fix perms, then relaunch.

While the server is running, the same state is available as JSON at:

```bash
curl http://127.0.0.1:7777/api/v1/system/recovery
```

Fields: `status`, `issue`, `db_version`, `app_supported_version`, `db_path`, `data_dir`, `backup_dir`, `next_steps`.

## 7. "I want to give my colleague a clean demo"

The cleanest path:

1. Run a full reset (section 1).
2. Launch Axion.
3. Import `sample_portfolio.csv` from the project root for representative data.
4. Wait 1–2 minutes for the first collection cycle (or skip — the empty Events tab is also a valid demo of "nothing happens yet").
5. Walk through Portfolio → Insights → Alerts → Settings.

## Files this guide intentionally does not touch

- The project folder (`src/`, `dashboard/`, `config/`, etc.) — it's the app.
- Your shell rc files — Axion does not modify them.
- macOS launchd or Windows scheduled tasks — the local launcher does not register any.

If you ran the **legacy** `install.sh`, it may have created launchd plists at `~/Library/LaunchAgents/com.kleitos.*.plist`. To remove them:

```bash
launchctl unload ~/Library/LaunchAgents/com.kleitos.core.plist 2>/dev/null
launchctl unload ~/Library/LaunchAgents/com.kleitos.openclaw.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.kleitos.*.plist
```

The current local-launcher path (`run_local.sh`) does not use launchd at all.
