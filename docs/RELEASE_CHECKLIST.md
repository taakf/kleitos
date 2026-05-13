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

## Cross-platform validation

Before publishing a release, the GitHub Actions workflow **`Release Local App Validation`** must pass on both runners:

- `windows-latest`
- `macos-latest`

It runs automatically on every pull request to `main`, and can be triggered manually via the **Actions → Release Local App Validation → Run workflow** button. The workflow definition is at [`.github/workflows/release-local-app.yml`](../.github/workflows/release-local-app.yml).

What the workflow proves on **each** OS:

- [ ] Python compilation succeeds (`compileall`)
- [ ] Test suite passes (`pytest`)
- [ ] In-process end-to-end smoke passes (`scripts/smoke_local.py` — 16 checks)
- [ ] Launcher syntax is valid (`bash -n` on macOS, PowerShell AST parse on Windows)
- [ ] Release zips build and verify (`scripts/build_release_zip.py`)
- [ ] Real local server startup works (`scripts/smoke_server_startup.py` — boots uvicorn on a temp DB, hits `/api/v1/health` and `/dashboard/`, then shuts down cleanly)
- [ ] Smoke from inside the extracted release zip passes (proves the zip itself is shippable)

If both jobs are green, **the Windows path is fully validated**. There is no separate manual Windows validation step required for release.

### Manual fallback if CI is unavailable

If for any reason CI cannot run (e.g. the workflow file is broken, GitHub is down, or you need to release urgently from a fork), reproduce the same gates manually:

1. On a fresh Windows 10/11 machine with Python 3.11+ on PATH:
   ```powershell
   python -m pip install -r requirements.txt pytest pytest-asyncio
   python scripts\smoke_local.py             # expect 16/16
   python scripts\build_release_zip.py
   python scripts\smoke_server_startup.py    # expect all checks PASS
   ```
2. On a fresh macOS machine with Python 3.11+:
   ```bash
   python -m pip install -r requirements.txt pytest pytest-asyncio
   python scripts/smoke_local.py             # expect 16/16
   python scripts/build_release_zip.py
   python scripts/smoke_server_startup.py    # expect all checks PASS
   ```
3. Tester sign-off:
   - [ ] Tester name: ____________
   - [ ] OS / version: ____________
   - [ ] Python version: ____________

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
| Cross-platform CI green (`Release Local App Validation` — both `macos-latest` and `windows-latest`) | | |

When every box above is ticked, the build is **customer-ready**.

## macOS launcher options

This repo ships two macOS launchers. They now share the same data-dir convention (`~/axion-data` default, `~/kleitos-data` honoured for back-compat), but they target different audiences.

| Launcher | When to use | Status |
|----------|-------------|--------|
| **`./scripts/run_local.sh`** | Anyone with a terminal. Foreground process, Ctrl+C to stop. | **Recommended.** Verified by the smoke test. |
| **`Axion.app`** | Customers who prefer double-clicking from Finder. Installs a launchd auto-start agent. | Working, but not code-signed. Gatekeeper requires right-click → Open on first launch. Verified manually only. |

If you are unsure, recommend `run_local.sh`. It has the smaller blast radius (no launchd auto-start, no `/Applications` install, no Finder PATH issues).
