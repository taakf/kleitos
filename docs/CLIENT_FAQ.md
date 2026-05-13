# Axion — Client FAQ

**Is this live data?**
Yes. News events are collected from real RSS feeds (Federal Reserve, ECB, Google News, MarketWatch). Holdings are imported from your portfolio file. All classifications run in real time.

**What works without AI?**
Everything you see in the demo: macro factor classification, relationship graph, risk alerts, severity filtering, recommended actions, digest generation, inbox, operator overrides, audit trail, exports, deep links, and saved views. The core engine is fully deterministic.

**What does AI add?**
Conversational natural-language queries, per-holding impact analysis, richer narrative digests, and PDF/image portfolio extraction. AI is additive — the core platform runs independently.

**Is it auditable?**
Yes. Every classification traces back to a specific event and keyword match. Every operator action is logged with timestamps. Every recommendation links to its evidence via rationale references.

**Is it portfolio-safe?**
Yes. Portfolio isolation is enforced at every layer — database queries, API responses, and UI rendering are all scoped to the active portfolio. One portfolio's data never leaks into another.

**Can operators override the model?**
Yes. Operators can override any factor sensitivity weight, add or remove relationships, run seed reconciliation from a YAML config, and backfill the link pipeline. All overrides are audited.

**Is there an inbox / workflow layer?**
Yes. The unified inbox aggregates alerts, digests, operator actions, and high-priority recommended actions with read/unread state. Actions can be dismissed and will reappear only when the underlying signal materially changes.

**What happens after deployment?**
Connect an Anthropic API key to unlock AI features. Configure Telegram for mobile delivery. Add custom news sources. Set up additional portfolios. The platform is ready for production use.
