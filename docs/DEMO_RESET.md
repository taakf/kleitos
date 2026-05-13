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

Axion writes a daily SQLite backup to `~/axion-data/backups/` (timestamped). To roll back:

1. Stop Axion (Ctrl+C).
2. Pick the backup you want, e.g. `~/axion-data/backups/kleitos-2026-05-13.db`.
3. Replace the live DB:
   ```bash
   cp ~/axion-data/backups/kleitos-2026-05-13.db ~/axion-data/db/kleitos.db
   ```
4. Relaunch.

The migration system will leave the schema as-is if it's already at head; otherwise it will upgrade in place.

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
