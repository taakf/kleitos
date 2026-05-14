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

**What does the Insights → Overview sub-tab do?**
It's a deterministic, evidence-backed roll-up of everything else in the app (Phase 12). Each card carries a severity, a category, the source rows it came from, and a deep link to the surface that explains it. The AI narrate toggle is optional — when enabled and a provider is configured, the wording can be rewritten, but the AI is constrained: it cannot add new holdings, new percentages, or new claims. Rewrites that mention an untrusted ticker are dropped. With no AI key, the page still works.

**What is the *What changed* panel on Insights → Overview? (Phase 14)**
A deterministic history deck above the card grid. It reads the local `insight_snapshots` table written by Run-now or the scheduler and tells you which cards were new / escalated / unchanged over the last 7 / 30 / 90 days. There is a sparkline of daily transitions and a list with deep links back to the underlying surface. No live prices, no investment advice, no AI prompts — only the snapshot data Phase 13 already wrote. Saved views now pin Insights Overview filters too: category, severity, time window, AI-narrate toggle.

**How do Insight notifications work? (Phase 13)**
Each generator run fingerprints every card on its deterministic content (severity, category, evidence, affected holdings). The notifier compares against `insight_snapshots` and labels each card `new` / `escalated` / `unchanged`. `new` and `escalated` cards above the inbox severity floor flow into the **Inbox** sub-tab with read/unread state; when Telegram is configured they also get a single push for cards at severity **high** or higher. Re-running with no material change does nothing — the fingerprint is identical, the badge stays "Already notified". Manual **Run now** button and a **Last generated** timestamp are available on the Overview sub-tab. AI narration never moves the fingerprint, so AI re-wording can't produce a fake notification.

**Does the Insights page give investment advice?**
No. Axion's deterministic insights are operational signals — concentration warnings, upcoming corporate events, data gaps, factor touchpoints — grounded in your portfolio's stored rows. They are not personalised investment advice or buy/sell recommendations, and they never use live market prices.

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

**What is the difference between Insights → News and the top-level Events tab?**
*News* (Insights → News) is the published news feed — stories collected from public sources, classified by macro factor, materiality, etc. *Events* (top-level Events tab) is the **scheduled** corporate / issuer calendar — earnings dates, dividends, AGMs, corporate actions. They live in two different tables and two different APIs (`/api/v1/events` vs `/api/v1/corporate-events`) so the lifecycles don't interfere.

**Why is the ATHEX automation marked "unsupported"?**
Athens Exchange does not currently publish a stable public machine-readable corporate-events feed. The Phase 9 release ships the full table + API + calendar UI plus a CSV-import drawer, so corporate events are usable today without inventing data. If you have an internal feed you trust, the source becomes a simple parser hook (`src/corporate_events/athex.py`).

**Does the "Listing country" chart show where companies make money?**
No — it shows where the **instrument is listed** (derived from the ISIN prefix or the exchange). The separate **Revenue geography** card answers the "where does the company earn money?" question, but only after you upload a CSV breakdown via its *Import CSV* button. Until you upload, Revenue geography shows an honest "No revenue geography uploaded yet" state — Axion will not infer revenue geography from listing country, sector, or any other proxy.

**How do I upload revenue geography?**
On the Portfolio → Exposures tab, click *Import CSV* in the Revenue geography card. Required columns: `region`, `revenue_share`, plus at least one of `ticker` / `isin`. Optional: `country`, `company_name`, `fiscal_year`, `period`, `currency`, `source_name`, `source_url`. `revenue_share` accepts `0.45`, `45`, or `45%`. Rows match to holdings by ISIN first, then ticker — scoped to the active portfolio. Per-row errors and per-company "sum < 100 %" warnings are returned without aborting the batch; the leftover flows into an *Other / unallocated* bucket so the chart still totals correctly.

**Can the AI read an annual report PDF for me?**
Yes — the dialog has a second tab, *AI extract from report*. Upload the PDF (or paste the regional-revenue passage as text) and Axion calls the configured AI vision provider with a strict anti-hallucination prompt. The AI is told **not** to infer revenue from headquarters, listing exchange, ISIN prefix, customer names, or country of incorporation, and to return an empty list if the document has no explicit geographic revenue breakdown. The dialog shows the candidates in an editable table with confidence + evidence text per row; **nothing is saved until you click *Confirm***. PDF bytes are processed in memory and never written to disk; the support bundle records counts only, never document content. Without an AI provider configured the tab reports `missing_key` and manual CSV remains the supported path.
