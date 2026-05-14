# Axion — Client FAQ

**Is this live data?**
Yes. News is collected from real public RSS feeds (Federal Reserve, ECB, Google News, MarketWatch, WSJ Markets, Seeking Alpha, Investing.com). Holdings are imported from your portfolio file. All classifications run on the data Axion has actually fetched.

**Which sources need a key?**
Most don't. The bundled defaults are public RSS — no keys, no signup. Two optional API-key sources are wired up: **NewsAPI** (`NEWSAPI_KEY`) and **Finnhub** (`FINNHUB_KEY`). While the key is missing they show as *Missing key* in Settings → News Sources; that's expected, not an error. Subscription / paid sources (Bloomberg, FactSet, Refinitiv, S&P Capital IQ) are not bundled.

**What works without AI?**
Everything you see in the demo: macro factor classification, relationship graph, risk alerts, severity filtering, recommended actions, digest generation, inbox, operator overrides, audit trail, exports, deep links, and saved views. The core engine is fully deterministic.

**What does AI add?**
Conversational natural-language queries, per-holding impact analysis, richer narrative digests, and PDF/image portfolio extraction. AI is additive — the core platform runs independently.

**Which AI providers are supported?**
Anthropic (Claude), OpenAI / ChatGPT, and Google Gemini. Pick one as primary in Settings → AI Configuration. Optionally set a second as backup; Axion will fall back automatically on rate-limit / 5xx / auth errors from the primary. Keys are stored at `~/.axion.env` with 600 permissions and never leave your machine except to call the provider you configured. The Settings UI has a **Test** button per key that reports a typed status (Active / Invalid key / Quota / Unreachable / Misconfigured / Error) without exposing the key.

**Is OAuth supported?**
No. Axion does not yet integrate with brokers, Google / Microsoft accounts, or any OAuth-authenticated data source. See `docs/OAUTH_ROADMAP.md` for the design intent.

**Is it auditable?**
Yes. Every classification traces back to a specific event and keyword match. Every operator action is logged with timestamps. Every recommendation links to its evidence via rationale references.

**Is it portfolio-safe?**
Yes. Portfolio isolation is enforced at every layer — database queries, API responses, and UI rendering are all scoped to the active portfolio. One portfolio's data never leaks into another.

**Can operators override the model?**
Yes. Operators can override any factor sensitivity weight, add or remove relationships, run seed reconciliation from a YAML config, and backfill the link pipeline. All overrides are audited.

**Is there an inbox / workflow layer?**
Yes. The unified inbox aggregates alerts, digests, operator actions, and high-priority recommended actions with read/unread state. Actions can be dismissed and will reappear only when the underlying signal materially changes.

**What happens after deployment?**
Connect an AI key (Anthropic, OpenAI / ChatGPT, or Google Gemini) to unlock the optional AI features. Configure Telegram for mobile delivery if you want push notifications. Add custom news sources. Set up additional portfolios. The platform is ready for use whether you add AI or not.

**How do I find a specific news item?**
Go to **Insights → News**. The filter bar above the table is server-side: search (debounced) queries title + summary; Source / Type / Factor / Materiality narrow the slice; the 24h / 7d / 30d pills set the published-at window; **Linked holdings only** drops everything that didn't match a holding; **Reset** clears all filters. Save the configuration as a Saved View when you want to come back to the same slice — restoring re-applies every filter.

**What do the chips on a news row mean?**
**Linked** means the story matched at least one of your holdings (direct ticker hit or factor channel). **Macro signal** means the deterministic factor classifier tagged it (interest rate, oil, FX, etc.) — clicking the row opens the news-item modal with the chain. The chips never mean "AI said so": they reflect deterministic rule outputs.

**Are source URLs safe to click?**
Yes. Before any URL is shown in the UI or written to the support bundle, `apiKey=`, `api_key=`, `token=`, `Bearer …`, and similar parameters are replaced with `***`. The original key (if any) never leaves the local backend.
