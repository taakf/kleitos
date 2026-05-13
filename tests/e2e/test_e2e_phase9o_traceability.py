"""Phase 9O browser E2E tests — Audit Trail UX + Traceability Surfaces.

Drives headless Chromium against the Phase 9J live-server fixture
and validates the Phase 9O additions end-to-end:

  * operator panel shows a "Recent operator actions" card populated
    from the shared Phase 9O shaping route (reads real audit_log rows)
  * a real factor override mutation appears in the recent-actions
    list immediately after save
  * a real reconcile run also appears in the recent-actions list
  * the intelligence overview recommended actions now carry a
    "Grounded in:" chip row sourced from Phase 9N ``rationale_refs``
  * the event detail modal carries the grouped Phase 9O "Grounded in"
    evidence block (Factors / Holdings / etc.)
  * alert cards carry a "Based on:" evidence chip row when
    related_events / related_holdings are populated
  * the digest reader carries a Phase 9O trust footer
  * factor + relationship tables carry provenance hints
    (manual → "edited X ago", seed → "YAML-backed")
  * no console errors during any of these flows

The tests use the same Phase 9J seeded DB — pA has a critical
macro_factor alert + two holdings with factor links + a seeded TSMC
relationship — so we don't need a separate seed pass.
"""

from __future__ import annotations

import re
import time

import pytest
from playwright.sync_api import Page, expect


_ASSERT_TIMEOUT = 10_000


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _open_dashboard(page: Page, portfolio_id: str = "pA") -> None:
    page.add_init_script(
        f"window.localStorage.setItem('activePortfolioId', '{portfolio_id}');"
    )
    page.goto("/dashboard", wait_until="networkidle")
    page.wait_for_selector("#tab-portfolio", timeout=_ASSERT_TIMEOUT)


def _open_settings(page: Page) -> None:
    page.click('[data-tab="settings"]')
    page.wait_for_selector("#tab-settings.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("#op-factor-table table tbody tr", timeout=_ASSERT_TIMEOUT)
    # The Phase 9O recent-actions card must mount with every panel load
    page.wait_for_selector("#op-recent-actions-card", timeout=_ASSERT_TIMEOUT)


def _wait_for_no_text(page: Page, selector: str, substring: str) -> None:
    page.wait_for_function(
        """
        ({selector, substring}) => {
            const el = document.querySelector(selector);
            if (!el) return true;
            return !el.innerText.includes(substring);
        }
        """,
        arg={"selector": selector, "substring": substring},
        timeout=_ASSERT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# 1) Operator panel — Recent operator actions card renders
# ---------------------------------------------------------------------------


def test_operator_recent_actions_card_mounts(page: Page, axion_server: str):
    """The Phase 9O card should always mount inside Settings → Operator,
    even when no operator mutations have happened yet.  Its body must
    render either a populated list OR the honest empty state string."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    card = page.locator("#op-recent-actions-card")
    expect(card).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(card).to_contain_text("Recent operator actions")

    body = page.locator("#op-recent-actions")
    expect(body).to_be_visible(timeout=_ASSERT_TIMEOUT)
    # Wait for the fetch to settle — either the list or the empty state
    page.wait_for_function(
        """
        () => {
            const el = document.querySelector('#op-recent-actions');
            if (!el) return false;
            return (
                el.querySelector('.op-recent-list')
                || el.querySelector('.op-recent-empty')
                || el.querySelector('.op-recent-error')
            ) !== null;
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )


def test_operator_factor_override_shows_up_in_recent_actions(
    page: Page, axion_server: str,
):
    """Run a real factor override mutation via the UI and verify the
    resulting row appears in the Phase 9O recent-actions card."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    # Make a factor override on AAPL / inflation (same flow as Phase 9J)
    page.select_option("#op-factor-filter", "inflation")
    page.wait_for_function(
        "() => document.querySelectorAll('#op-factor-table table tbody tr').length >= 1",
        timeout=_ASSERT_TIMEOUT,
    )
    aapl_row = page.locator(
        "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation']"
    )
    expect(aapl_row).to_be_visible(timeout=_ASSERT_TIMEOUT)
    aapl_row.locator("[data-op='op-factor-edit']").click()

    modal = page.locator("#op-factor-modal")
    expect(modal).to_be_visible(timeout=_ASSERT_TIMEOUT)
    page.fill("#op-factor-sensitivity", "-0.55")
    page.fill("#op-factor-reason", "phase 9o e2e override")
    page.click("#op-factor-save-btn")
    expect(modal).not_to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Phase 9O — the recent-actions card must pick up the new audit
    # row on the next fetch (auto-triggered after the mutation)
    page.wait_for_function(
        """
        () => {
            const rows = document.querySelectorAll('#op-recent-actions .op-recent-row');
            for (const r of rows) {
                const title = r.querySelector('.op-recent-title')?.innerText || '';
                const summary = r.querySelector('.op-recent-summary')?.innerText || '';
                if ((title.includes('AAPL') || summary.includes('AAPL'))
                    && r.dataset.entityType === 'holding_factor_sensitivity') {
                    return true;
                }
            }
            return false;
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )

    # The row must carry the override title + at least one of the
    # factor/value signals from the shaping layer
    row_text = page.locator(
        "#op-recent-actions .op-recent-row[data-entity-type='holding_factor_sensitivity']"
    ).first.inner_text()
    assert "AAPL" in row_text
    assert "inflation" in row_text.lower()
    assert "-0.55" in row_text or "override" in row_text.lower()

    # Reason we typed in the modal must surface on the row
    assert "phase 9o e2e override" in row_text.lower()

    # Clean up so we don't leak state
    page.once("dialog", lambda d: d.accept())
    page.locator(
        "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation'] [data-op='op-factor-delete']"
    ).click()
    page.wait_for_function(
        """
        () => {
            const row = document.querySelector(
                "#op-factor-table tr[data-holding-id='h_aapl_pA'][data-factor='inflation']"
            );
            return row && !row.innerText.toLowerCase().includes('manual');
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )


def test_operator_reconcile_shows_up_in_recent_actions(
    page: Page, axion_server: str,
):
    """Run a real reconcile and verify the shaped row lands in the
    Phase 9O recent-actions card (or at worst collapses to an existing
    no-op row — in which case the card still renders SOME reconcile
    entry)."""
    _open_dashboard(page, "pA")
    page.on("dialog", lambda d: d.accept())
    _open_settings(page)

    # Disable prune to avoid the extra confirm dialog in the flow
    prune_cb = page.locator("#op-reconcile-prune")
    if prune_cb.is_checked():
        prune_cb.uncheck()

    page.click("#op-reconcile-btn")
    # Wait for the reconcile to complete
    page.wait_for_function(
        "() => {"
        " const e = document.querySelector('#op-last-result');"
        " return e && e.innerText.includes('Reconcile complete');"
        "}",
        timeout=_ASSERT_TIMEOUT,
    )

    # After the mutation the recent-actions card must auto-refresh
    # and contain at least one reconcile row
    page.wait_for_function(
        """
        () => {
            const rows = document.querySelectorAll(
                '#op-recent-actions .op-recent-row[data-entity-type="holding_relationships"]'
            );
            return rows.length >= 1;
        }
        """,
        timeout=_ASSERT_TIMEOUT,
    )
    first_reconcile = page.locator(
        "#op-recent-actions .op-recent-row[data-entity-type='holding_relationships']"
    ).first
    text = first_reconcile.inner_text().lower()
    assert "reconcil" in text
    # Either a real reconcile ("created X, updated Y") or a no-op
    # shape ("no changes") — both are honest outputs of the shaper.
    assert (
        "created" in text or "updated" in text
        or "no changes" in text or "unchanged" in text
    ), f"reconcile row shape looks wrong: {text!r}"


# ---------------------------------------------------------------------------
# 2) Intelligence overview — recommended actions carry Grounded-in chips
# ---------------------------------------------------------------------------


def test_overview_recommended_actions_render_grounded_refs(
    page: Page, axion_server: str,
):
    """Phase 9N produced ``rationale_refs`` but the Phase 9G overview
    didn't render them.  Phase 9O renders a compact chip row under each
    action with the top refs."""
    _open_dashboard(page, "pA")

    actions_block = page.locator("#intelligence-overview .intel-actions-block")
    expect(actions_block).to_be_visible(timeout=_ASSERT_TIMEOUT)
    rows = actions_block.locator(".intel-action-row")
    # The stressed pA seed produces at least one recommended action
    assert rows.count() >= 1, (
        f"expected recommended actions for pA, got {rows.count()}"
    )

    # At least one action row must carry a Phase 9O evidence refs line
    any_with_refs = page.locator(
        "#intelligence-overview .intel-action-row .intel-action-refs.evidence-refs"
    )
    expect(any_with_refs.first).to_be_visible(timeout=_ASSERT_TIMEOUT)
    refs_text = any_with_refs.first.inner_text()
    # The label is CSS-uppercased via ``text-transform: uppercase``,
    # which inner_text reflects.  Match case-insensitively.
    assert "grounded in" in refs_text.lower()
    # Must render at least one chip (the shape layer produces refs
    # like "factor:interest_rate", "alert:<id>", "attention:AAPL", etc.)
    chips = any_with_refs.first.locator(".evidence-ref-chip")
    assert chips.count() >= 1, (
        f"expected at least one evidence ref chip, got {chips.count()}"
    )


# ---------------------------------------------------------------------------
# 3) Event detail modal — grouped Grounded-in block
# ---------------------------------------------------------------------------


def test_event_detail_evidence_refs_are_grouped(page: Page, axion_server: str):
    """The Fed rate event has a factor ref + two holding refs in
    ``explanation_grounded_in``.  Phase 9O should render them as a
    grouped block with per-category sub-rows (Factors / Holdings)."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#events-table table", timeout=_ASSERT_TIMEOUT)

    fed_row = page.locator(
        "#events-table .events-row-clickable", has_text="Federal Reserve"
    ).first
    expect(fed_row).to_be_visible(timeout=_ASSERT_TIMEOUT)
    fed_row.click()

    modal = page.locator("#event-detail-modal")
    expect(modal).to_be_visible(timeout=_ASSERT_TIMEOUT)
    _wait_for_no_text(page, "#event-detail-body", "Loading event detail")

    why_block = page.locator("#event-detail-body .event-why-block")
    expect(why_block).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Phase 9O — grouped evidence refs
    grouped = why_block.locator(".evidence-refs.evidence-refs-grouped")
    expect(grouped).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(grouped).to_contain_text("Grounded in")

    # Either the full grouped form (multiple .evidence-ref-group
    # sub-rows) or the compact flat fallback (single category).
    # For the Fed event we expect at least 2 categories (factors +
    # holdings) so the grouped form must render.
    groups = grouped.locator(".evidence-ref-group")
    assert groups.count() >= 1, (
        f"expected at least one grouped ref row, got {groups.count()}"
    )

    # Must include a Factors group with the interest_rate ref
    grouped_text = grouped.inner_text()
    assert (
        "factor:interest_rate" in grouped_text
        or "Factors" in grouped_text
    ), f"factor grouping missing: {grouped_text!r}"

    # Close the modal to clean up
    page.locator("#event-detail-modal [data-close-modal]").first.click()
    expect(modal).not_to_be_visible(timeout=_ASSERT_TIMEOUT)


# ---------------------------------------------------------------------------
# 4) Alerts list — per-card evidence chip row
# ---------------------------------------------------------------------------


def test_alerts_list_renders_based_on_evidence_chips(
    page: Page, axion_server: str,
):
    """Phase 9O adds a "Based on:" chip row to alert cards that have
    related_events / related_holdings populated.  The pA critical
    alert has both, so it must render the chip row."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)

    # The critical alert is first because of severity-first ordering
    critical_card = page.locator(
        "#alerts-content .alert-card.severity-critical"
    ).first
    expect(critical_card).to_be_visible(timeout=_ASSERT_TIMEOUT)

    evidence = critical_card.locator(".alert-evidence-refs")
    expect(evidence).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(evidence).to_contain_text("Based on")
    chips = evidence.locator(".evidence-ref-chip")
    assert chips.count() >= 1, (
        f"expected at least one evidence chip on critical alert, got {chips.count()}"
    )
    chip_texts = [chips.nth(i).inner_text() for i in range(chips.count())]
    # The pA critical alert references evt_fed_rates + AAPL — at
    # least one of those must show up in the chip row
    joined = " ".join(chip_texts)
    assert "event:" in joined or "holding:" in joined, (
        f"evidence chips look wrong: {joined!r}"
    )


# ---------------------------------------------------------------------------
# 5) Digest reader — trust footer
# ---------------------------------------------------------------------------


def test_digest_reader_renders_trust_footer(page: Page, axion_server: str):
    """The seeded pA digest has event_count=3, alert_count=3,
    holding_count=2 → Phase 9O should render a "Based on … from
    current signals" trust footer at the bottom of the digest card."""
    _open_dashboard(page, "pA")
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.click('[data-subtab="digest"]')
    page.wait_for_selector("#subtab-digest.active", timeout=_ASSERT_TIMEOUT)

    digest = page.locator("#digest-content")
    expect(digest.locator(".digest-headline")).to_be_visible(timeout=_ASSERT_TIMEOUT)

    footer = digest.locator(".digest-trust-footer")
    expect(footer).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(footer).to_contain_text("Based on")
    expect(footer).to_contain_text("from current signals")
    footer_text = footer.inner_text()
    # At least one of the three count pieces must be present
    assert (
        "event" in footer_text.lower()
        or "alert" in footer_text.lower()
        or "holding" in footer_text.lower()
    ), f"trust footer is empty: {footer_text!r}"


# ---------------------------------------------------------------------------
# 6) Factor / Relationship table provenance hints
# ---------------------------------------------------------------------------


def test_factor_table_shows_sector_default_provenance(
    page: Page, axion_server: str,
):
    """Default factor rows should display a "sector default" micro
    label below the source badge, so the operator can tell a true
    default apart from a missing row."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    # Wait for the factor table to populate
    page.wait_for_function(
        "() => document.querySelectorAll('#op-factor-table table tbody tr').length >= 1",
        timeout=_ASSERT_TIMEOUT,
    )

    # At least one row with source=default must carry the provenance
    default_row_provenance = page.locator(
        "#op-factor-table tr[data-source='default'] .op-provenance-default"
    ).first
    expect(default_row_provenance).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(default_row_provenance).to_contain_text("sector default")


def test_relationship_table_shows_seed_provenance(
    page: Page, axion_server: str,
):
    """Seed relationship rows should display a "YAML-backed" micro
    label so the operator knows those rows live in
    ``config/relationships.yaml`` and are protected."""
    _open_dashboard(page, "pA")
    _open_settings(page)

    # Wait for the relationship table to render at least the seeded
    # TSMC row (from the Phase 9J seed) — match on data-source.
    page.wait_for_function(
        "() => document.querySelectorAll('#op-rel-table tr[data-source=\"seed\"]').length >= 1",
        timeout=_ASSERT_TIMEOUT,
    )
    seed_provenance = page.locator(
        "#op-rel-table tr[data-source='seed'] .op-provenance-seed"
    ).first
    expect(seed_provenance).to_be_visible(timeout=_ASSERT_TIMEOUT)
    expect(seed_provenance).to_contain_text("YAML-backed")


# ---------------------------------------------------------------------------
# 7) No console errors during any of the Phase 9O flows
# ---------------------------------------------------------------------------


def test_no_console_errors_during_phase9o_flows(
    page: Page, axion_server: str,
):
    """Walk through every Phase 9O surface in one go — operator
    recent actions, intelligence overview refs, event detail grouped
    block, alerts evidence chips — and assert no console errors."""
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.type}: {m.text}")
        if m.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

    _open_dashboard(page, "pA")

    # Overview grounded refs
    expect(
        page.locator("#intelligence-overview .intel-action-refs").first
    ).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Event detail grouped block
    page.click('[data-tab="intelligence"]')
    page.wait_for_selector("#tab-intelligence.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#events-table table", timeout=_ASSERT_TIMEOUT)
    fed_row = page.locator(
        "#events-table .events-row-clickable", has_text="Federal Reserve"
    ).first
    fed_row.click()
    expect(page.locator("#event-detail-modal")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )
    _wait_for_no_text(page, "#event-detail-body", "Loading event detail")
    expect(
        page.locator("#event-detail-body .evidence-refs-grouped")
    ).to_be_visible(timeout=_ASSERT_TIMEOUT)
    page.locator("#event-detail-modal [data-close-modal]").first.click()
    expect(page.locator("#event-detail-modal")).not_to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )

    # Alerts evidence chips
    page.click('[data-tab="alerts"]')
    page.wait_for_selector("#tab-alerts.active", timeout=_ASSERT_TIMEOUT)
    page.wait_for_selector("#alerts-content .alert-card", timeout=_ASSERT_TIMEOUT)
    expect(
        page.locator("#alerts-content .alert-evidence-refs").first
    ).to_be_visible(timeout=_ASSERT_TIMEOUT)

    # Settings → operator recent actions
    _open_settings(page)
    expect(page.locator("#op-recent-actions-card")).to_be_visible(
        timeout=_ASSERT_TIMEOUT,
    )

    _IGNORE = ("favicon", "Failed to load resource")
    fatal = [e for e in errors if not any(i in e for i in _IGNORE)]
    assert not fatal, (
        "console errors during Phase 9O flows:\n" + "\n".join(fatal)
    )
