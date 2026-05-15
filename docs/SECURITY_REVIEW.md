# Axion — Security & Privacy Review

This document records the security and privacy posture of Axion as a
**local-first, single-user desktop application**. It is an honest
review, not a certification: it states what Axion does protect, what it
deliberately does *not* implement, and how to verify the guarantees.

- Release identity: `Axion 1.0.0 (local)` — see `src/version.py`.
- Scope: the downloadable, single-machine, loopback-only build. Not a
  hosted/multi-tenant service.

---

## 1. Threat model

Axion is designed for **one user running it on their own machine**.

| In scope | Out of scope |
|----------|--------------|
| Keeping API keys off disk in clear, off the wire, and out of logs / support bundles | Multi-user access control / RBAC |
| Not leaking customer portfolio data into exports, bundles, or release zips | Network adversaries beyond loopback |
| Honest provenance for the release artefacts | Hardened OS / full-disk-encryption (the user's responsibility) |
| Refusing to invent or exfiltrate data | Enterprise SSO / OAuth / secret vaults |

Axion binds **`127.0.0.1` only** (`src/config.py` `ApiSettings.host`),
serves a loopback dashboard, and performs no outbound calls except to
the public RSS feeds and the AI provider the user explicitly
configures. There is no telemetry, no "call home", no broker
connection, no cloud sync.

---

## 2. Secret storage

- AI provider API keys are stored in **`~/.axion.env`** (legacy
  `~/.kleitos.env` is also read for back-compat).
- The Settings UI writes keys via `_write_env_key()` in
  `src/api/routes/settings.py`. **Phase 22 hardened this**: after every
  write the file is `os.chmod`-ed to **`0600`** (owner read/write
  only), so the documented "600 permissions" guarantee is enforced by
  the code, not merely claimed. On filesystems/platforms without POSIX
  modes (e.g. Windows) the `chmod` is a harmless near no-op.
  - *Known minor limitation:* `write_text()` creates the file under
    the process umask and the `chmod` tightens it immediately after —
    a sub-millisecond window exists where a freshly-created file is at
    the umask default. For a single-user local machine this is
    accepted; a future hardening could create the file with
    `os.open(..., 0o600)`.
- In configuration objects keys are wrapped in pydantic `SecretStr`,
  so they render as `**********` if a settings object is printed.
- Keys never leave the machine except in the request to the provider
  the user configured.

## 3. Redaction — keys never leak into logs, diagnostics, or bundles

Four scrubbers exist and are unit-tested in
`tests/unit/test_phase22_security_privacy.py`:

| Scrubber | Location | Purpose |
|----------|----------|---------|
| `_redact_value` / `_redact_env` / `_scrub_inline` | `scripts/support_bundle.py` | Redact env vars by secret key-name *and* by vendor-token value pattern; mask URL-embedded keys + `Bearer` tokens in any text |
| `scrub_secrets` | `src/llm/provider_status.py` | Mask vendor key shapes in the `test-provider` status message |
| `scrub_source_error` | `src/sources/source_status.py` | Mask query-string keys, `Bearer` tokens, and vendor token patterns in any source error before it is stored/returned/logged |
| `_safe_str` | `src/api/routes/intelligence.py` | Redact forbidden substrings (keys, prompt bodies) from the Phase-15 insight export |

The vendor patterns covered: Anthropic (`sk-ant-…`), OpenAI
(`sk-proj-…`, `sk-…`), Google (`AIza…`), GitHub (`ghp_/gho_…`), Slack
(`xox…`), and Telegram bot tokens.

- **Diagnostics** (`GET /api/v1/system/diagnostics`) returns booleans
  and counts only — `llm_configured` / `telegram_configured` are
  booleans; no field carries key material.
- **Support bundle** (`scripts/support_bundle.py`) excludes `.db`
  files, raw `.env`, and uploaded-document bodies; it ships only
  redacted env/settings snapshots, log tails, and counts.

## 4. Uploaded documents

PDF / report uploads (portfolio import, AI revenue-geography
extraction) are processed **entirely in memory** — `pdfplumber` reads
from an `io.BytesIO`; no PDF bytes are written to disk
(`src/intelligence/revenue_geography/extraction.py`). The support
bundle records counts only, never document content.

## 5. Release provenance

`scripts/build_release_zip.py` writes a `RELEASE_MANIFEST.json` into
each zip recording app name / version / release channel / git commit /
build timestamp and explicit guarantees (no database files, no API
keys, no customer data). `verify_zip()` rejects any build that leaks a
`.env`, `*.db`, cache, or VCS path.

## 6. Deliberately NOT implemented

These are intentional non-goals for a local single-user build, and the
product documentation never claims them:

- **OAuth / SSO** — roadmap only (`docs/OAUTH_ROADMAP.md`).
- **Encryption-at-rest vault** for keys — keys rely on filesystem
  `0600` + the user's OS account, not an app-managed vault.
- **Broker sync, paid-vendor data feeds, live market prices** — none.
- **Multi-user auth / RBAC** — the API-key middleware is a single
  local shared secret, loopback-exempt; it is not an identity system.

## 7. Static analysis — known findings

`bandit -rq src scripts` reports **0 High** and **5 Medium** findings.
All five are **false positives** and are retained (not silenced with
broad `# nosec`):

| Finding | Location | Why it is a false positive |
|---------|----------|----------------------------|
| B310 (urllib open) | `scripts/axion-app.pyw:420` | Opens a hard-coded `https://` download URL — no user input |
| B310 (urllib open) | `scripts/axion-tray.pyw:51`, `:135` | Same — hard-coded `https://` URLs |
| B310 (urllib open) | `scripts/smoke_server_startup.py:55` | Hard-coded loopback health URL in a test smoke |
| B608 (SQL string) | `scripts/support_bundle.py:239` | Iterates a *hard-coded literal list* of table names; no user input reaches the query |

`ruff check src scripts` reports 2 retained findings (1 deliberate
`E402` lazy import, 1 cosmetic `E741`) — see the Phase 21 commit.

## 8. Optional manual dependency audit

A dependency vulnerability scan is **optional and manual** — it is *not*
a project dependency and *not* a CI gate. To run it on demand:

```bash
# one-off, in the project venv — pip-audit is NOT bundled
python -m pip install pip-audit
python -m pip_audit
```

`requirements.txt` uses `>=` floor pins (the common local-app
tradeoff: simpler installs vs a fully-reproducible lockfile). Operators
who need reproducibility can freeze their own lockfile with
`pip freeze`.

## 9. How to verify

```bash
python -m pytest -q tests/unit/test_phase22_security_privacy.py
python -m pytest -q tests/unit/test_phase4_support_diagnostics.py
python scripts/support_bundle.py            # inspect the redacted zip
python scripts/build_release_zip.py         # verify_zip rejects leaks
bandit -rq src scripts                      # expect 0 High, 5 FP Medium
```
