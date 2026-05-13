"""Phase 9I frontend/backend contract guard tests for the Operator UI.

This repo has no browser harness, so Phase 9I follows the Phase 9B
guard pattern:

1. **HTML / JS / CSS alignment** — every ``#op-...`` id the JS reads
   is actually declared in ``index.html``; every CSS class the JS
   renders has a matching rule in ``styles.css``; every class-name
   prefix that's constructed dynamically (``op-last-result-*``) has
   all of its expected variants defined.
2. **JS → API alignment** — every Phase 9H endpoint the operator UI
   is supposed to call is actually present in the ``API`` object in
   ``app.js``, and every API constant the UI references has a matching
   route registered in the FastAPI app.
3. **Markup contract** — the Operator section markup is present in
   ``index.html`` at the expected location (inside the Settings tab)
   and the two modals (factor override + manual relationship) exist.
4. **Tab loader wire-up** — ``tabLoaders.settings`` calls
   ``loadOperatorPanel``, so switching to Settings always refreshes
   the operator panel for the active portfolio.
5. **Portfolio-safety wiring** — the operator loader reads
   ``_activePortfolioId`` (so switching portfolios re-scopes the
   tables) and passes ``portfolio_id`` on every operator fetch.
6. **Source-protection wire-up** — seed / default / ai_inferred rows
   never expose a delete button in the JS renderer output.  The test
   reads the source strings out of the renderer so a future refactor
   can't quietly re-enable destructive actions on read-only rows.

The tests are intentionally coarse — they don't spin up a browser —
but they lock down the contract so refactors can't silently drift.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
APP_JS = REPO_ROOT / "dashboard" / "js" / "app.js"
INDEX_HTML = REPO_ROOT / "dashboard" / "index.html"
STYLES_CSS = REPO_ROOT / "dashboard" / "css" / "styles.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) Phase 9I HTML ids the JS expects must all exist in index.html
# ---------------------------------------------------------------------------


_PHASE9I_REQUIRED_IDS = frozenset({
    # Panel top
    "op-active-portfolio",
    "op-last-result",
    # Factor card
    "op-factor-card",
    "op-factor-filter",
    "op-factor-refresh",
    "op-factor-table",
    # Factor modal
    "op-factor-modal",
    "op-factor-modal-title",
    "op-factor-modal-holding",
    "op-factor-modal-factor",
    "op-factor-modal-current",
    "op-factor-sensitivity",
    "op-factor-reason",
    "op-factor-save-btn",
    "op-factor-delete-btn",
    # Relationship card
    "op-rel-card",
    "op-rel-source-filter",
    "op-rel-refresh",
    "op-rel-add",
    "op-rel-table",
    # Relationship modal
    "op-rel-modal",
    "op-rel-modal-title",
    "op-rel-holding",
    "op-rel-type",
    "op-rel-related-ticker",
    "op-rel-related-entity-key",
    "op-rel-related-name",
    "op-rel-strength",
    "op-rel-description",
    "op-rel-reason",
    "op-rel-save-btn",
    # Maintenance card
    "op-actions-card",
    "op-reconcile-btn",
    "op-reconcile-prune",
    "op-reconcile-status",
    "op-backfill-btn",
    "op-backfill-window",
    "op-backfill-max",
    "op-backfill-status",
})


def test_every_phase9i_id_exists_in_html():
    html = _read(INDEX_HTML)
    missing = [
        i for i in sorted(_PHASE9I_REQUIRED_IDS)
        if f'id="{i}"' not in html
    ]
    assert not missing, f"Phase 9I ids missing from index.html: {missing}"


def test_every_phase9i_id_is_referenced_by_js():
    """Any id the JS no longer references is dead markup — fail so we
    clean it up instead of letting the HTML rot."""
    js = _read(APP_JS)
    # op-actions-card, op-factor-card, op-rel-card are CSS-only anchors.
    css_only = {"op-actions-card", "op-factor-card", "op-rel-card"}
    unused = [
        i for i in sorted(_PHASE9I_REQUIRED_IDS - css_only)
        if i not in js
    ]
    assert not unused, f"Phase 9I ids declared in HTML but never read by JS: {unused}"


# ---------------------------------------------------------------------------
# 2) Phase 9I CSS classes must all have matching rules
# ---------------------------------------------------------------------------


_PHASE9I_JS_CLASSES = frozenset({
    "op-source-badge",
    "op-src-manual", "op-src-seed", "op-src-ai", "op-src-default", "op-src-zero",
    "op-factor-table",
    "op-rel-table",
    "op-rel-locked",
    "op-last-result",
    "op-empty",
})

_PHASE9I_HTML_CLASSES = frozenset({
    "op-panel-card",
    "op-panel-portfolio",
    "op-card-actions",
    "op-action-row",
    "op-action-block",
    "op-action-title",
})

# Dynamically-constructed variants (the JS builds
# "op-last-result-" + tone) — every expected tone must have a rule.
_PHASE9I_DYNAMIC_CLASSES = frozenset({
    "op-last-result-ok",
    "op-last-result-err",
    "op-last-result-info",
})

_PHASE9I_ALL_CLASSES = (
    _PHASE9I_JS_CLASSES | _PHASE9I_HTML_CLASSES | _PHASE9I_DYNAMIC_CLASSES
)


def test_every_phase9i_class_has_a_css_rule():
    css = _read(STYLES_CSS)
    missing = []
    for cls in sorted(_PHASE9I_ALL_CLASSES):
        pattern = re.compile(rf"\.{re.escape(cls)}(?![a-zA-Z0-9_-])")
        if not pattern.search(css):
            missing.append(cls)
    assert not missing, (
        f"Phase 9I references {len(missing)} CSS class(es) with no "
        f"matching rule in dashboard/css/styles.css: {missing}"
    )


def test_every_phase9i_js_class_still_emitted_by_js():
    """Sanity: confirm the JS renderer actually emits every JS-class
    we guard.  HTML-only classes and dynamic variants are checked
    separately."""
    js = _read(APP_JS)
    unused = [cls for cls in sorted(_PHASE9I_JS_CLASSES) if cls not in js]
    assert not unused, (
        f"JS-emitted classes no longer appear in dashboard/js/app.js: "
        f"{unused}. Either re-add them or remove from the guard list."
    )


def test_every_phase9i_html_class_still_declared():
    html = _read(INDEX_HTML)
    unused = [cls for cls in sorted(_PHASE9I_HTML_CLASSES) if cls not in html]
    assert not unused, (
        f"Phase 9I HTML classes no longer declared in index.html: {unused}"
    )


# ---------------------------------------------------------------------------
# 3) Phase 9I API constants must be defined in the JS API object AND
#    map to a real backend route
# ---------------------------------------------------------------------------


_PHASE9I_API_CONSTANTS = frozenset({
    "opFactorSensitivities",
    "opFactorOverrides",
    "opFactorOverrideById",
    "opRelationships",
    "opRelationshipById",
    "opRelationshipsReconcile",
    "opBackfill",
    "opFactorTaxonomy",
})


def test_every_phase9i_api_constant_defined_in_js():
    js = _read(APP_JS)
    missing = [name for name in sorted(_PHASE9I_API_CONSTANTS) if name not in js]
    assert not missing, (
        f"Phase 9I API constants missing from dashboard/js/app.js: {missing}"
    )


def test_every_phase9i_api_constant_hits_real_backend_route():
    """Cross-check: every operator endpoint the UI references is
    actually registered in the FastAPI app.  This catches "I removed
    a route and forgot to update the dashboard" regressions."""
    from src.main import app
    registered = {r.path for r in app.routes if hasattr(r, "path")}
    expected = {
        "/api/v1/operator/factor-sensitivities",
        "/api/v1/operator/factor-sensitivities/overrides",
        "/api/v1/operator/factor-sensitivities/overrides/{override_id}",
        "/api/v1/operator/relationships",
        "/api/v1/operator/relationships/{rel_id}",
        "/api/v1/operator/relationships/reconcile",
        "/api/v1/operator/backfill",
        "/api/v1/operator/taxonomy/factors",
    }
    missing = expected - registered
    assert not missing, (
        f"Phase 9H backend routes referenced by the operator UI are not "
        f"registered in src/main.py: {missing}"
    )


# ---------------------------------------------------------------------------
# 4) Tab loader wire-up + markup location
# ---------------------------------------------------------------------------


def test_tab_loaders_settings_calls_operator_panel_loader():
    js = _read(APP_JS)
    # The Phase 9G audit proved how this indirection works: the JS has
    # a `tabLoaders.settings = function () { ... loadOperatorPanel(); ... }`
    # block.  We just assert the call is present inside a settings-loader
    # definition.
    pattern = re.compile(
        r"tabLoaders\.settings\s*=\s*function\s*\([^)]*\)\s*\{[^}]*loadOperatorPanel\s*\(",
        re.DOTALL,
    )
    assert pattern.search(js), (
        "tabLoaders.settings does not call loadOperatorPanel() — the "
        "operator panel won't refresh when the user opens Settings or "
        "switches portfolios."
    )


def test_operator_section_nested_inside_settings_tab():
    html = _read(INDEX_HTML)
    # Extract the Settings tab panel (id="tab-settings") and verify
    # the operator section-title is inside it.  Uses a coarse substring
    # search that survives minor HTML reflows but catches
    # "accidentally moved the whole section out" bugs.
    start = html.find('id="tab-settings"')
    assert start != -1, "Settings tab panel not found in index.html"
    end = html.find('<!-- Holding Detail Slide-out -->', start)
    if end == -1:
        end = len(html)
    settings_block = html[start:end]
    assert 'section-title" style="margin-top:1.25rem;">Operator<' in settings_block, (
        "Operator section not found inside the Settings tab panel"
    )
    assert 'id="op-factor-table"' in settings_block
    assert 'id="op-rel-table"' in settings_block
    assert 'id="op-reconcile-btn"' in settings_block
    assert 'id="op-backfill-btn"' in settings_block


def test_operator_modals_exist_in_html():
    html = _read(INDEX_HTML)
    assert 'id="op-factor-modal"' in html
    assert 'id="op-rel-modal"' in html


# ---------------------------------------------------------------------------
# 5) Portfolio-safety wiring
# ---------------------------------------------------------------------------


def test_operator_loader_is_portfolio_scoped():
    js = _read(APP_JS)
    # The operator loader must reach ``_activePortfolioId`` when it
    # fetches factor sensitivities + relationships.  We grep for the
    # two fetch call sites and confirm they compose portfolio_id.
    assert "portfolio_id: _activePortfolioId" in js, (
        "Operator loader does not pass portfolio_id — cross-portfolio "
        "leakage risk in the operator panel."
    )
    # And the panel renders the active portfolio id in the header
    assert "#op-active-portfolio" in js
    # And it reads _pq() for the holdings cache so holdings modal is scoped
    assert "_pq(API.holdings)" in js


def test_switch_portfolio_invalidates_settings_cache():
    """``switchPortfolio`` invalidates every tab cache so the next
    time the operator opens Settings the operator panel reloads for
    the new portfolio."""
    js = _read(APP_JS)
    # The switch function uses `Object.keys(tabLoaded).forEach(k => tabLoaded[k] = false)`
    # — we just assert the pattern survives.
    assert "tabLoaded[k] = false" in js
    assert "switchPortfolio = function" in js


# ---------------------------------------------------------------------------
# 6) Source-protection wire-up
# ---------------------------------------------------------------------------


def test_factor_delete_button_only_for_manual_source():
    """The factor-table renderer must only attach a delete button to
    ``source === 'manual'`` rows.  If a refactor ever adds a delete
    button to seed/default/ai_inferred rows, this test must fail."""
    js = _read(APP_JS)
    # Extract the renderer function body
    start = js.find("function renderOperatorFactorTable(rows)")
    assert start != -1, "renderOperatorFactorTable not found"
    end = js.find("\n    }\n", start)
    assert end != -1, "renderOperatorFactorTable end not found"
    body = js[start:end]
    # The delete-button variable must be guarded by r.source === 'manual'
    assert "r.source === 'manual'" in body, (
        "Factor delete button not guarded by source === 'manual' — "
        "non-manual rows could be rendered with destructive actions"
    )
    # And the guard must enclose the data-op='op-factor-delete' emission
    assert "op-factor-delete" in body


def test_relationship_delete_button_only_for_manual_source():
    js = _read(APP_JS)
    start = js.find("function renderOperatorRelationshipTable(rows)")
    assert start != -1, "renderOperatorRelationshipTable not found"
    end = js.find("\n    }\n", start)
    assert end != -1, "renderOperatorRelationshipTable end not found"
    body = js[start:end]
    # Every mutating button in the relationship row must be gated on
    # r.source === 'manual'; the else branch must render the lock icon.
    assert "r.source === 'manual'" in body, (
        "Relationship mutating buttons not guarded by source === 'manual'"
    )
    assert "op-rel-locked" in body, (
        "Non-manual rows must render the locked indicator instead of "
        "edit/delete buttons"
    )
    # The edit/delete data-op hooks must exist
    assert "op-rel-edit" in body
    assert "op-rel-delete" in body


def test_client_refuses_delete_on_non_manual_relationship():
    """Client-side guard: the JS delete handler rejects attempts to
    delete non-manual rows even if someone manually dispatches the
    event.  Defense-in-depth for the server-side 409."""
    js = _read(APP_JS)
    start = js.find("async function _opDeleteRelationship")
    assert start != -1, "_opDeleteRelationship not found"
    end = js.find("\n    }\n", start)
    body = js[start:end]
    assert "row.source !== 'manual'" in body, (
        "Client-side delete guard missing — would rely solely on the "
        "backend 409 which is brittle UX."
    )


# ---------------------------------------------------------------------------
# 7) Reconcile + backfill UX safety
# ---------------------------------------------------------------------------


def test_reconcile_prune_requires_explicit_confirmation():
    js = _read(APP_JS)
    start = js.find("async function _opRunReconcile")
    assert start != -1, "_opRunReconcile not found"
    end = js.find("\n    }\n", start)
    body = js[start:end]
    # Prune branch must show a confirm() dialog
    assert "prune" in body
    assert "confirm(" in body, (
        "Reconcile with prune=true must require an explicit confirm()"
    )
    # Prune=true is the server default — the confirm must mention that
    # manual rows are NOT touched, otherwise operators get scared
    assert "Manual" in body or "manual" in body


def test_backfill_confirms_before_running():
    js = _read(APP_JS)
    start = js.find("async function _opRunBackfill")
    assert start != -1, "_opRunBackfill not found"
    end = js.find("\n    }\n", start)
    body = js[start:end]
    assert "confirm(" in body, "Backfill must require a confirm() dialog"
    # Must pass window_days + max_events in the POST body
    assert "window_days" in body
    assert "max_events" in body
    # And render stats back from the response
    assert "links_added" in body
    assert "mfe_added" in body


def test_backfill_window_bound_is_safe_only():
    """The window dropdown must only offer 1/7/14/30 — matching the
    ``MAX_WINDOW_DAYS = 30`` backend hard cap.  A future refactor that
    adds "90d" or "all history" as an option must fail this test."""
    html = _read(INDEX_HTML)
    start = html.find('id="op-backfill-window"')
    assert start != -1, "op-backfill-window select not found"
    end = html.find("</select>", start)
    block = html[start:end]
    values = set(re.findall(r'value="(\d+)"', block))
    # Only values allowed: 1, 7, 14, 30 (MAX_WINDOW_DAYS)
    assert values <= {"1", "7", "14", "30"}, (
        f"op-backfill-window offers values outside the safe range: {values}"
    )
    # And every value must be <= MAX_WINDOW_DAYS
    from src.intelligence.backfill import MAX_WINDOW_DAYS
    for v in values:
        assert int(v) <= MAX_WINDOW_DAYS


# ---------------------------------------------------------------------------
# 8) Success-path sanity — loader populates cached rows so the
#     click-handlers can look them up
# ---------------------------------------------------------------------------


def test_operator_caches_rows_for_modal_lookup():
    js = _read(APP_JS)
    assert "_opFactorRowsCache" in js
    assert "_opRelRowsCache" in js
    # Both caches must be populated by the loader fetch sites
    assert "_opFactorRowsCache = Array.isArray(rows)" in js
    assert "_opRelRowsCache = Array.isArray(rows)" in js


def test_operator_shows_last_result_after_actions():
    """_opShowLastResult must be called by reconcile, backfill, and
    both CRUD paths so the operator always gets a visible echo.
    We check for the expected title string LITERALS present anywhere
    in the source — the JS mixes single-line and multi-line call
    styles, so a strict regex would be brittle.

    Phase 9L note: the ``Reconcile failed`` / ``Backfill failed``
    titles are now constructed inside ``_opHandleOperatorError`` via
    ``${actionTitle} failed`` instead of literal strings, so we look
    for the helper function + action labels instead of the old literals.
    """
    js = _read(APP_JS)
    assert "_opShowLastResult(" in js
    expected_titles = [
        "Override saved",
        "Override deleted",
        "Manual relationship created",
        "Manual relationship deleted",
        "Relationship updated",
        "Reconcile complete",
        "Backfill complete",
    ]
    missing = [t for t in expected_titles if t not in js]
    assert not missing, (
        f"Expected last-result titles missing from dashboard/js/app.js: {missing}"
    )
    # Phase 9L: the failure path is routed through _opHandleOperatorError
    # which builds the title via ${actionTitle} failed.  Confirm both
    # the helper and its usage are wired.
    assert "_opHandleOperatorError" in js
    assert "${actionTitle} failed" in js
