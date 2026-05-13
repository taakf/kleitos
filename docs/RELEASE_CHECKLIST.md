# Axion — Release readiness checklist

What to verify before declaring a build customer-ready. Run this in order. Don't skip steps.

## A. Source state

- [ ] `git status` is clean.
- [ ] `git log --oneline -5` matches the release notes.
- [ ] You are on the branch you intend to release (`main`, by default).
- [ ] No uncommitted secrets in `.env`, `~/.axion.env`, or anywhere in the tree.

```bash
git status
git log --oneline -5
grep -RIn "sk-ant-\|sk-proj-\|ghp_" --include='*.py' --include='*.md' --include='*.sh' --include='*.ps1' src scripts docs README* 2>/dev/null
```

The grep should return **no matches**.

## B. Static checks

Run from the project root with the venv active.

- [ ] **All Python files compile**
  ```bash
  python -m compileall -q src scripts tests
  ```
  Must exit 0.

- [ ] **Tests pass**
  ```bash
  python -m pytest -q
  ```
  All tests green.

- [ ] **Linter clean** (warnings allowed, no fatals)
  ```bash
  python -m ruff check src tests scripts
  ```

- [ ] **No new security findings**
  ```bash
  python -m bandit -q -r src scripts
  ```

- [ ] **Bash launcher is syntactically valid**
  ```bash
  bash -n scripts/run_local.sh
  ```

- [ ] **PowerShell launcher is syntactically valid** (on Windows, or anywhere with PowerShell installed)
  ```powershell
  $tokens = $null; $errors = $null
  [System.Management.Automation.Language.Parser]::ParseFile('scripts\run_local.ps1', [ref]$tokens, [ref]$errors)
  $errors
  ```
  Output must be empty.

## C. End-to-end smoke

- [ ] **Local smoke test passes**
  ```bash
  python scripts/smoke_local.py
  ```
  Must report `16/16 passed`.

## D. Fresh-machine simulation

Wipe everything and run as if a customer just downloaded the project.

- [ ] Run reset: `rm -rf .venv ~/axion-data ~/.axion.env`
- [ ] Run launcher: `./scripts/run_local.sh` (or `scripts\run_local.ps1`)
- [ ] Launcher reports `Axion is running.` within 2 minutes.
- [ ] Browser opens `http://127.0.0.1:7777/dashboard/` automatically (macOS / Windows).
- [ ] Dashboard renders without console errors.
- [ ] Portfolio tab shows the **default** portfolio (id `default`, name "Main Portfolio").
- [ ] Empty states are present and graceful (no "undefined", no stack traces).
- [ ] Health endpoint reports `status: ok` or `degraded`, never a 500.
  ```bash
  curl -s http://127.0.0.1:7777/api/v1/health | python -m json.tool
  ```

## E. CSV import flow

- [ ] Drag `sample_portfolio.csv` into the dashboard.
- [ ] Review screen shows 10 rows.
- [ ] Click Import → success toast, rows appear in the Holdings table.
- [ ] Portfolio → Exposures shows non-zero sector and currency breakdowns.

## F. Settings / AI

- [ ] **Without** an API key:
  - Settings → AI Provider shows "Not configured."
  - POST `/api/v1/settings/test-provider` returns `status: disabled` or `unreachable` (never 500).
  - Assistant tab degrades gracefully (no fake LLM output).
- [ ] **With** a real key (if you're shipping with one):
  - Settings → Test reports `active`.
  - Assistant tab returns real model responses.

## G. Stop / restart

- [ ] Ctrl+C in the launcher cleanly shuts down (no orphaned uvicorn process).
- [ ] Relaunching keeps the imported holdings and any settings.

## H. Doc consistency

- [ ] `README.md` points at `README_LOCAL.md` and `docs/CUSTOMER_QUICKSTART.md`.
- [ ] `README_LOCAL.md` matches what the launchers actually do.
- [ ] `docs/CUSTOMER_QUICKSTART.md` mentions the AI features as optional.
- [ ] `KNOWN_LIMITATIONS.md` is current — no removed features still listed.

## I. Repo hygiene

- [ ] No stale duplicate source trees in the project root (no `Axion/`, no `Axion-Installers/`, no `Axion.app/` unless it has a real binary).
- [ ] `dist/` is either empty or gitignored.
- [ ] `~/axion-data/`, `.venv/`, `__pycache__/`, `*.db` are gitignored.
- [ ] `gh repo view --json defaultBranchRef` confirms `origin/main` matches local HEAD.

## J. Release artifact (only if shipping a zip)

- [ ] `python scripts/build_release_zip.py` produces `dist/axion-windows.zip` and `dist/axion-macos.zip`.
- [ ] Each zip contains `src/`, `dashboard/`, `config/`, `scripts/run_local.*`, `requirements.txt`, `sample_portfolio.csv`, `README_LOCAL.md`, `docs/`, `.env.template`.
- [ ] Each zip **excludes** `.git`, `.venv`, `__pycache__`, `dist`, `~/axion-data`, and any `Axion/` / `Axion-Installers/` duplicates.
- [ ] Extracting the zip on a clean machine, then running the launcher, reaches a healthy dashboard.

## Windows validation still required

The PowerShell launcher (`scripts/run_local.ps1`) was authored on macOS and **not parse-checked against a real Windows PowerShell host** in the release branch. Before tagging a build for Windows customers, run the following on a clean Windows 10 or 11 machine and tick every box.

### Pre-conditions

- [ ] Fresh Windows 10/11 user account (or `~/.venv`, `~/axion-data\`, `~/.axion.env` deleted).
- [ ] Python 3.11+ installed from <https://www.python.org/downloads/> with **"Add Python to PATH"** ticked.
- [ ] Open **PowerShell** in the project directory.

### 1. Parse-check the script

```powershell
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    'scripts\run_local.ps1', [ref]$tokens, [ref]$errors
)
$errors
```

Expected: output is empty (no parse errors).

### 2. Launch via PowerShell

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
```

Expected console output, in order:
- [ ] `[OK]    Python 3.X (python ...)`
- [ ] `[INFO]  Creating virtual environment at .venv ...` then `[OK]    Virtual environment created`
- [ ] `[INFO]  Installing dependencies ...` then `[OK]    Dependencies installed` (1–2 minutes, first run only)
- [ ] `[OK]    Data dir ready (C:\Users\<user>\axion-data)`
- [ ] `[INFO]  Running migrations ...` then `migrations: ok` then `[OK]    Database is at schema head`
- [ ] `[INFO]  Starting Axion on http://127.0.0.1:7777 ...`
- [ ] Within 30s: `Axion is running.` banner.

The default browser should open `http://127.0.0.1:7777/dashboard/` automatically.

No red `[ERROR]` lines should appear.

### 3. Launch via the `.bat`

```cmd
Axion.bat
```

Expected: same flow as the PowerShell path (the `.bat` may fall through to the tray app if `pystray` / `Pillow` are installed, or to uvicorn directly otherwise).

- [ ] No CMD window stays open if the tray app starts.
- [ ] Dashboard opens.

### 4. Health endpoint

```powershell
(Invoke-WebRequest http://127.0.0.1:7777/api/v1/health -UseBasicParsing).Content
```

- [ ] Returns JSON with `"status":"ok"` (or `"degraded"`) and `"database":"connected"`.
- [ ] Does not 500 or hang.

### 5. CSV import

In the dashboard:
- [ ] Drag `sample_portfolio.csv` from the project root onto the Holdings tab.
- [ ] The review screen shows 10 rows.
- [ ] Clicking Import returns success and shows the imported rows.

### 6. Smoke test

In a second PowerShell window:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_local.py
```

Expected: `Result: 16/16 passed, 0 failed`.

### 7. Port conflict path

While Axion is still running, in a new PowerShell:

```powershell
PowerShell -ExecutionPolicy Bypass -File scripts\run_local.ps1
```

Expected: a clean `Axion is already running at http://127.0.0.1:7777` line, **not** a stack trace or a long timeout. Dashboard opens.

### 8. Stop cleanly

- [ ] Ctrl+C in the original launcher window returns to the prompt within a few seconds.
- [ ] No orphan `python.exe` running uvicorn (verify with `Get-Process python | Select-Object Id, ProcessName, MainWindowTitle`).

### 9. Sign-off

- [ ] Tester name: ____________
- [ ] Windows build: ____________
- [ ] Python version: ____________
- [ ] All boxes above are ticked.

Until this section is signed off, **do not ship `axion-windows.zip` to customers as a generally-available release.** It is acceptable to ship as a beta to a Windows tester first.

---

## K. Sign-off

| Check | Owner | Date |
|-------|-------|------|
| Static checks (B) | | |
| Smoke test (C) | | |
| Fresh-machine sim (D) | | |
| CSV import (E) | | |
| Settings/AI (F) | | |
| Stop/restart (G) | | |
| Windows validation (above) | | |

When every box above is ticked, the build is **customer-ready**.

## macOS launcher options

This repo ships two macOS launchers. They now share the same data-dir convention (`~/axion-data` default, `~/kleitos-data` honoured for back-compat), but they target different audiences.

| Launcher | When to use | Status |
|----------|-------------|--------|
| **`./scripts/run_local.sh`** | Anyone with a terminal. Foreground process, Ctrl+C to stop. | **Recommended.** Verified by the smoke test. |
| **`Axion.app`** | Customers who prefer double-clicking from Finder. Installs a launchd auto-start agent. | Working, but not code-signed. Gatekeeper requires right-click → Open on first launch. Verified manually only. |

If you are unsure, recommend `run_local.sh`. It has the smaller blast radius (no launchd auto-start, no `/Applications` install, no Finder PATH issues).
