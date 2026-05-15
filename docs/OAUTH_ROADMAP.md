# OAuth — roadmap and design notes

> **OAuth is not part of the current customer build.** Axion does not ship
> any OAuth flow today. This document captures the design intent for a
> future integration so we have a stable shape to grow into when a concrete
> use case lands. Until then, every customer-facing surface (Settings tab,
> README_LOCAL, CUSTOMER_QUICKSTART) intentionally says nothing about
> OAuth — implementing or implying it would be misleading.

## Why OAuth is not implemented yet

1. **No concrete first integration has been chosen.** OAuth without a
   first-party provider to authorise against is plumbing without a load.
2. **The current customer flow does not need it.** News collection runs
   against public RSS feeds and free APIs; AI providers (Anthropic /
   OpenAI / Gemini) take a static API key entered locally and never call
   home from the OAuth perspective. CSV import is offline.
3. **OAuth has a non-trivial security surface** (token vault, refresh
   tokens, revocation, scopes, redirect URI handling, PKCE). It deserves
   its own phase, its own tests, and its own threat model — not a hasty
   bolt-on to ship a single feature.

## Candidate future use cases

Each row should justify itself before any OAuth code is written. None of
these is committed.

| Use case | Provider | Why OAuth, not an API key |
|---|---|---|
| **Broker / custodian holdings sync** | Interactive Brokers, Saxo, Trading 212, etc. | API keys for brokers are rare; OAuth is the standard mechanism for read-only account access. |
| **Paid financial data sources** | FactSet, Bloomberg API, S&P, Refinitiv | Some require OAuth client credentials flow rather than a static key. |
| **Gmail / Google Calendar** | Google | If we later add "ingest a results-day reminder from your Gmail" or "publish corporate events to your calendar." Speculative — only worth doing if a customer asks. |
| **Microsoft 365 (Outlook / Calendar)** | Microsoft | Same logic as Gmail, for Microsoft-shop customers. |
| **ATHEX corporate events authenticated feeds** | ATHEX | If ATHEX exposes a paywalled corporate-actions feed in the future. Today the public scrape path does not need OAuth. |

OAuth will NOT be added for:

- AI providers (Anthropic / OpenAI / Gemini) — they use static API keys.
- RSS / public APIs that already work without auth.
- Anything we cannot demonstrate a working test fixture for.

## Security principles (when we do build it)

1. **Local token vault.** Tokens are encrypted at rest under a key
   derived from `~/.axion.env` or a platform keychain entry. Never
   serialise tokens to logs, support bundles, or diagnostics endpoints.
2. **No tokens in the support bundle.** `scripts/support_bundle.py`
   already redacts patterns like `gho_*` and `ghp_*`; add the
   `<provider>_oauth_token` family before the first OAuth provider lands.
3. **Per-provider scopes, narrowest viable.** Read-only by default.
   Every scope expansion needs a customer-facing change-log entry.
4. **Localhost redirect flow with PKCE.** Listener binds to a high port
   on `127.0.0.1`, accepts a single OAuth callback, then shuts the
   listener down. No persistent web server, no public callback URL.
5. **Explicit revocation.** Settings UI shows the active OAuth grants
   and lets the user revoke each one. Revocation deletes the local
   token AND calls the provider's revocation endpoint.
6. **Diagnostics:** `/api/v1/system/diagnostics` reports the number of
   OAuth grants configured (count only, no provider names or scopes if
   they could narrow the identity surface).
7. **Tests:** every flow tested against a mocked provider; never call
   real OAuth endpoints in CI.

## Suggested implementation phases (when a first use case lands)

This is the **shape**, not a commitment. Order it however the first
integration demands.

1. **Architecture spike.** Pick the first provider. Build one full
   round-trip in a feature branch. Decide on token-vault encryption,
   keychain backend per OS, redirect-port allocation, scope shape.
2. **Local token vault.** Pure storage layer with encryption + tests.
   Two operations: `put(provider, scopes, token, refresh_token,
   expires_at)` and `get(provider) -> Token | None`. No I/O outside the
   data dir; never logs token material.
3. **First provider end-to-end.** Auth code grant + PKCE, refresh,
   revoke. Tests against a mocked authorisation server.
4. **Settings UI surface.** "Connect" / "Disconnect" / "Status" rows in
   the AI & Integrations panel; per-provider rows; visible scopes; last
   refresh timestamp. Never the raw token, never the auth code.
5. **Revocation + cleanup.** When the customer revokes, both the
   provider call and the local vault row are deleted; if either fails,
   the UI says so honestly.
6. **Diagnostics + support bundle.** Add OAuth-grant count to
   `/api/v1/system/diagnostics`. Add `<provider>_oauth_token` patterns to
   `scripts/support_bundle.py`'s `_SECRET_*` lists.
7. **Tests.** Unit (token vault round-trip, redaction), integration
   (mocked OAuth flow), e2e (Settings UI click-through against the
   mocked authorisation server).

## Anti-goals

- **No silent token refresh that runs in the background while the user
  is offline.** Refresh on demand only, when a feature actually needs it.
- **No OAuth scopes wider than the feature needs**, even if the provider
  recommends bundling.
- **No multi-tenant model.** Axion is single-user local. Each install's
  tokens belong to that one user; we do not store organisation IDs we
  could later weaponise.
- **No marketing of OAuth before it ships.** The Settings UI must not
  show greyed-out "Coming soon — Connect with Google" buttons. Until
  there is a working flow, the surface stays absent.

## How this doc gets retired

When the first OAuth integration ships:

1. Move this file under `docs/architecture/` and renumber its sections.
2. Replace the "OAuth is not part of the current customer build" banner
   at the top with a status table of which providers are wired and which
   are not.
3. Update `README_LOCAL.md` and `docs/CUSTOMER_QUICKSTART.md` with the
   user-facing steps.
4. Add the OAuth section to `docs/RELEASE_CHECKLIST.md`.

Until that happens, treat anything implying OAuth in customer-facing
copy as a documentation bug.
