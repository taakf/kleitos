/**
 * Axion Dashboard — Client-side Application
 * Full portfolio management with CRUD operations
 */
(function () {
    'use strict';

    // ================================================================
    // API Endpoints
    // ================================================================
    const API = {
        portfolios:   '/api/v1/portfolios',
        holdings:     '/api/v1/portfolio/holdings',
        holdingById:  (id) => `/api/v1/portfolio/holdings/${id}`,
        summary:      '/api/v1/portfolio/summary',
        exposure:     (dim) => `/api/v1/portfolio/exposure?dimension=${dim}`,
        trades:       '/api/v1/portfolio/trades',
        digestLatest: '/api/v1/digests/latest',
        digestGen:    '/api/v1/digests/generate',
        alerts:       '/api/v1/alerts',
        alertsActive: '/api/v1/alerts/active',
        alertAck:     (id) => `/api/v1/alerts/${id}/acknowledge`,
        audit:        '/api/v1/audit',
        health:       '/api/v1/health',
        agentStatus:  '/api/v1/agents/status',
        agentRuns:    '/api/v1/agents/runs',
        agentRun:     (id) => `/api/v1/agents/${id}/run`,
        sources:      '/api/v1/sources',
        sourceById:   (id) => `/api/v1/sources/${id}`,
        sourceEnable: (id) => `/api/v1/sources/${id}/enable`,
        sourceDisable:(id) => `/api/v1/sources/${id}/disable`,
        sourceHealth: (id) => `/api/v1/sources/${id}/health`,
        upload:       '/api/v1/portfolio/upload',
        extract:      '/api/v1/portfolio/extract',
        importReviewed: '/api/v1/portfolio/import-reviewed',
        events:       '/api/v1/events',
        eventsRecent: '/api/v1/events/recent',
        eventById:    (id) => `/api/v1/events/${id}`,
        analysisNotes:'/api/v1/analysis/notes',
        intelligenceSummary: '/api/v1/intelligence/summary',
        // Phase 9I — Operator control surface (read + write)
        opFactorSensitivities:   '/api/v1/operator/factor-sensitivities',
        opFactorOverrides:       '/api/v1/operator/factor-sensitivities/overrides',
        opFactorOverrideById:    (id) => `/api/v1/operator/factor-sensitivities/overrides/${id}`,
        opRelationships:         '/api/v1/operator/relationships',
        opRelationshipById:      (id) => `/api/v1/operator/relationships/${id}`,
        opRelationshipsReconcile:'/api/v1/operator/relationships/reconcile',
        opBackfill:              '/api/v1/operator/backfill',
        opFactorTaxonomy:        '/api/v1/operator/taxonomy/factors',
        // Phase 9L — live action status + audit readback
        opActionsStatus:         '/api/v1/operator/actions/status',
        auditEntries:            '/api/v1/audit',
        // Phase 9O — compact recent operator actions + ref categories
        auditRecent:             '/api/v1/audit/recent',
        auditCategories:         '/api/v1/audit/categories',
        // Phase 9P — Notification Center / inbox
        notificationsInbox:      '/api/v1/notifications',
        notificationsMarkRead:   '/api/v1/notifications/mark-read',
        notificationsMarkAllRead:'/api/v1/notifications/mark-all-read',
        // Phase 9U — saved views
        savedViews:              '/api/v1/views',
        // Phase 9T — action lifecycle state
        actionsEffective:        '/api/v1/actions/effective',
        actionsSetState:         '/api/v1/actions/set-state',
        actionsReadAll:          '/api/v1/actions/read-all',
        actionsClearState:       '/api/v1/actions/clear-state',
    };

    // ================================================================
    // Active Portfolio State
    // ================================================================
    // Stored in localStorage, validated on load, drives all portfolio-scoped requests.
    let _activePortfolioId = localStorage.getItem('activePortfolioId') || 'default';
    let _portfolioList = [];

    function getActivePortfolioId() { return _activePortfolioId; }

    function _pq(url) {
        // Append ?portfolio_id= to a URL for portfolio-scoped requests
        const sep = url.includes('?') ? '&' : '?';
        return `${url}${sep}portfolio_id=${encodeURIComponent(_activePortfolioId)}`;
    }

    async function _loadPortfolioSelector() {
        try {
            const list = await fetchJSON(API.portfolios);
            _portfolioList = list;
            const sel = document.getElementById('portfolio-select');
            if (!sel) return;

            // Validate active portfolio still exists
            const valid = list.find(p => p.id === _activePortfolioId);
            if (!valid && list.length > 0) {
                const def = list.find(p => p.is_default) || list[0];
                _activePortfolioId = def.id;
                localStorage.setItem('activePortfolioId', _activePortfolioId);
            }

            sel.innerHTML = list.map(p =>
                `<option value="${esc(p.id)}" ${p.id === _activePortfolioId ? 'selected' : ''}>${esc(p.name)}</option>`
            ).join('');
        } catch (e) {
            // Fallback: keep current selector state
        }
    }

    window.switchPortfolio = function(portfolioId) {
        _activePortfolioId = portfolioId;
        localStorage.setItem('activePortfolioId', portfolioId);
        // Invalidate all portfolio-scoped tab caches and reload active tab
        Object.keys(tabLoaded).forEach(k => tabLoaded[k] = false);
        const activeTab = document.querySelector('.tab-link.active');
        if (activeTab) {
            switchTab(activeTab.dataset.tab);
        }
    };

    window.openCreatePortfolio = function() {
        $('#cp-name').value = '';
        $('#cp-desc').value = '';
        const ccySel = document.getElementById('cp-ccy');
        if (ccySel) ccySel.value = 'USD';
        $('#create-portfolio-modal').showModal();
    };

    window.submitCreatePortfolio = async function() {
        const name = $('#cp-name').value.trim();
        if (!name) { showToast('Portfolio name is required', 'error'); return; }
        const desc = $('#cp-desc').value.trim() || null;
        const ccy = document.getElementById('cp-ccy')?.value || 'USD';

        await withLoading($('#create-portfolio-btn'), 'Creating...', async () => {
            try {
                const p = await postJSON(API.portfolios, { name, description: desc, base_currency: ccy });
                $('#create-portfolio-modal').close();
                showToast(`Portfolio "${p.name}" created`);
                // Switch to the new portfolio
                _activePortfolioId = p.id;
                localStorage.setItem('activePortfolioId', p.id);
                await _loadPortfolioSelector();
                Object.keys(tabLoaded).forEach(k => tabLoaded[k] = false);
                switchTab('portfolio');
            } catch (e) {
                showToast('Could not create portfolio: ' + e.message, 'error');
            }
        });
    };

    // ================================================================
    // Helpers
    // ================================================================
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    function esc(str) {
        if (str == null) return '';
        const d = document.createElement('div');
        d.textContent = String(str);
        return d.innerHTML;
    }

    //: Phase 9Q — proper HTML-attribute escaper.  ``esc()`` above
    //: uses ``textContent → innerHTML`` which encodes ``<``, ``>``
    //: and ``&`` but leaves ``"`` and ``'`` alone.  That's fine for
    //: element content but BREAKS when the escaped string is used
    //: inside a ``"..."`` attribute value (the first embedded quote
    //: closes the attribute early).  This helper explicitly encodes
    //: both quote characters so ``data-nav-target="${escAttr(json)}"``
    //: round-trips intact through ``getAttribute`` + ``JSON.parse``.
    function escAttr(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    // ================================================================
    // Phase 9L — structured API error
    // ================================================================
    //
    // The old fetch layer collapsed every non-2xx response into a plain
    // ``Error(message)``, which meant the UI couldn't distinguish a
    // 429 rate-limit from a 500 crash or a 409 in-progress from a 409
    // identity-collision.  Phase 9L keeps the ``.message`` field
    // backward-compatible (so existing ``catch (e)`` branches still
    // work) but additionally attaches:
    //
    //   e.status         — numeric HTTP status
    //   e.body           — the raw parsed JSON body (or {} if empty)
    //   e.isRateLimit    — true iff status === 429 AND bucket is set
    //   e.bucket         — rate-limit bucket name (Phase 9K)
    //   e.retryAfter     — retry-after seconds (from body or header)
    //   e.limitPerMinute — bucket ceiling (Phase 9K)
    //   e.isInProgress   — true iff status === 409 AND detail.in_progress
    //   e.action         — which action is in progress (reconcile|backfill)
    //
    // Callers that want the old behavior keep working because the
    // string form of the error remains readable.
    class ApiError extends Error {
        constructor(message, { status, body, url } = {}) {
            super(message);
            this.name = 'ApiError';
            this.status = status || 0;
            this.body = body || {};
            this.url = url || '';

            // Phase 9K 429 shape: {detail, bucket, limit_per_minute, retry_after_seconds}
            this.bucket = this.body.bucket || null;
            this.limitPerMinute = this.body.limit_per_minute || null;
            this.retryAfter = (
                this.body.retry_after_seconds != null
                    ? Number(this.body.retry_after_seconds)
                    : null
            );
            this.isRateLimit = status === 429;

            // Phase 9K 409 in-progress shape:
            //   {detail: {detail, in_progress: true, action: "reconcile"|"backfill"}}
            // FastAPI wraps HTTPException.detail under a top-level "detail" key,
            // so we unwrap one level here.
            const inner = this.body.detail;
            if (
                status === 409
                && inner
                && typeof inner === 'object'
                && inner.in_progress === true
            ) {
                this.isInProgress = true;
                this.action = inner.action || null;
                // Prefer the inner human-readable detail for the .message
                if (inner.detail) {
                    this.message = String(inner.detail);
                }
            } else {
                this.isInProgress = false;
                this.action = null;
            }
        }
    }

    async function _parseErrorBody(res) {
        try {
            return await res.clone().json();
        } catch (_e) {
            return {};
        }
    }

    async function _throwForStatus(res, url) {
        const body = await _parseErrorBody(res);
        // Build a human-readable primary message for backward compat.
        // FastAPI dict detail or string detail both need to be handled.
        let detailText;
        if (body && typeof body.detail === 'string') {
            detailText = body.detail;
        } else if (body && typeof body.detail === 'object' && body.detail?.detail) {
            detailText = String(body.detail.detail);
        } else if (body && body.detail) {
            try { detailText = JSON.stringify(body.detail); } catch { detailText = ''; }
        }
        const msg = detailText || `HTTP ${res.status}`;
        // Fill in retry-after from header if the body didn't carry it.
        if (body && body.retry_after_seconds == null) {
            const ra = res.headers.get('Retry-After');
            if (ra) body.retry_after_seconds = Number(ra);
        }
        throw new ApiError(msg, { status: res.status, body, url });
    }

    async function fetchJSON(url) {
        const res = await fetch(url);
        if (!res.ok) {
            if (res.status === 404) return null;
            await _throwForStatus(res, url);
        }
        return res.json();
    }

    async function postJSON(url, data) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) {
            await _throwForStatus(res, url);
        }
        return res.json();
    }

    async function putJSON(url, data) {
        const res = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!res.ok) {
            await _throwForStatus(res, url);
        }
        return res.json();
    }

    async function deleteJSON(url) {
        const res = await fetch(url, { method: 'DELETE' });
        if (!res.ok) {
            await _throwForStatus(res, url);
        }
        return res.json();
    }

    function formatDate(iso) {
        if (!iso) return '\u2014';
        const d = new Date(iso);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    function formatDateShort(iso) {
        if (!iso) return '\u2014';
        const d = new Date(iso);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }

    function timeAgo(iso) {
        if (!iso) return '';
        const s = Math.floor((Date.now() - new Date(iso)) / 1000);
        if (s < 60) return 'just now';
        if (s < 3600) return Math.floor(s / 60) + 'm ago';
        if (s < 86400) return Math.floor(s / 3600) + 'h ago';
        return Math.floor(s / 86400) + 'd ago';
    }

    function formatCurrency(val, ccy) {
        if (val == null) return '\u2014';
        return new Intl.NumberFormat('en-US', {
            style: 'currency', currency: ccy || 'USD',
            minimumFractionDigits: 0, maximumFractionDigits: 0
        }).format(val);
    }

    function formatNum(val, dec = 2) {
        if (val == null) return '\u2014';
        return new Intl.NumberFormat('en-US', {
            minimumFractionDigits: dec, maximumFractionDigits: dec
        }).format(val);
    }

    function formatPct(val) {
        if (val == null) return '\u2014';
        return (val >= 0 ? '+' : '') + (val * 100).toFixed(1) + '%';
    }

    function pnlClass(val) {
        if (val == null) return '';
        return val >= 0 ? 'text-success' : 'text-danger';
    }

    function titleCase(str) {
        if (!str) return '';
        const small = new Set(['a','an','the','and','but','or','nor','for','in','on','at','to','by','of','up','as','is','no']);
        return str.replace(/_/g, ' ').replace(/\b[A-Za-z0-9]+/g, (w, i) => {
            // Preserve all-caps words (tickers, acronyms like SHEL, AAPL, USD)
            if (w === w.toUpperCase() && w.length >= 2 && /[A-Z]/.test(w)) return w;
            const lower = w.toLowerCase();
            if (i > 0 && small.has(lower)) return lower;
            return lower.charAt(0).toUpperCase() + lower.slice(1);
        });
    }

    function severityBadge(sev) {
        const s = (sev || 'info').toLowerCase();
        const map = { critical: 'badge-critical', high: 'badge-high', medium: 'badge-medium', warning: 'badge-warning', info: 'badge-info', low: 'badge-info' };
        return `<span class="badge ${map[s] || 'badge-muted'}">${esc(titleCase(s))}</span>`;
    }

    function statusDot(status) {
        const s = (status || '').toLowerCase();
        const map = { ok: 'status-ok', healthy: 'status-ok', running: 'status-running', operational: 'status-ok',
                      connected: 'status-ok', active: 'status-ok',
                      degraded: 'status-degraded', idle: 'status-idle', warning: 'status-degraded',
                      stopped: 'status-degraded',
                      down: 'status-down', error: 'status-error', failed: 'status-error' };
        return `<span class="status-dot ${map[s] || 'status-idle'}"></span>`;
    }

    function formatUptime(seconds) {
        if (!seconds) return '\u2014';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        if (h > 24) return Math.floor(h / 24) + 'd ' + (h % 24) + 'h';
        return h + 'h ' + m + 'm';
    }

    function ensureArray(data, ...keys) {
        if (Array.isArray(data)) return data;
        for (const k of keys) {
            if (data && Array.isArray(data[k])) return data[k];
        }
        return [];
    }

    async function withLoading(btn, loadingText, asyncFn) {
        const original = btn.textContent;
        btn.disabled = true;
        btn.textContent = loadingText;
        try { return await asyncFn(); }
        finally { btn.disabled = false; btn.textContent = original; }
    }

    function renderError(msg) {
        return `<div class="error-state">Failed to load ${esc(msg)}</div>`;
    }

    // ── SVG icon library (24×24 line-art for empty states) ──
    const ICONS = {
        portfolio: '<svg viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="M7 16l4-5 4 3 5-6"/></svg>',
        events:    '<svg viewBox="0 0 24 24"><path d="M4 4h16v16H4z" rx="2"/><path d="M4 9h16"/><path d="M9 4v5"/><path d="M8 13h3"/><path d="M8 16h5"/></svg>',
        analysis:  '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/><path d="M8 11h6"/><path d="M11 8v6"/></svg>',
        digest:    '<svg viewBox="0 0 24 24"><path d="M6 2h9l5 5v13a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/><path d="M14 2v5h5"/><path d="M8 13h8"/><path d="M8 17h5"/></svg>',
        alerts:    '<svg viewBox="0 0 24 24"><path d="M12 2L3 20h18L12 2z"/><path d="M12 9v4"/><circle cx="12" cy="16" r="0.5" fill="currentColor"/></svg>',
        check:     '<svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4L12 14.01l-3-3"/></svg>',
        upload:    '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>',
        audit:     '<svg viewBox="0 0 24 24"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M9 14l2 2 4-4"/></svg>',
    };

    function svgIcon(name) {
        return ICONS[name] ? `<div class="empty-icon">${ICONS[name]}</div>` : '';
    }

    function renderEmpty(icon, text, opts) {
        // icon can be an SVG name (string key in ICONS) or raw HTML
        const o = opts || {};
        let iconHtml = '';
        if (icon && ICONS[icon]) {
            iconHtml = svgIcon(icon);
        } else if (icon) {
            iconHtml = `<div class="icon">${icon}</div>`;
        }
        let html = `<div class="empty-state">${iconHtml}<p>${esc(text)}</p>`;
        if (o.hint) html += `<p class="text-sm text-muted mt-2">${esc(o.hint)}</p>`;
        if (o.actions && o.actions.length) {
            html += `<div class="mt-3" style="display:flex;gap:0.5rem;justify-content:center;flex-wrap:wrap;">`;
            o.actions.forEach(a => {
                html += `<button class="btn ${a.primary ? 'btn-primary' : 'btn-outline'}" onclick="${esc(a.onclick)}">${esc(a.label)}</button>`;
            });
            html += `</div>`;
        }
        html += `</div>`;
        return html;
    }

    function renderSkeleton(rows = 5) {
        let html = '<div class="skeleton-band"><div class="skeleton skeleton-cell w-lg"></div><div class="skeleton skeleton-cell w-md"></div><div class="skeleton skeleton-cell w-sm"></div></div>';
        for (let i = 0; i < rows; i++) {
            html += `<div class="skeleton-row"><div class="skeleton skeleton-cell w-md"></div><div class="skeleton skeleton-cell w-xl"></div><div class="skeleton skeleton-cell w-md"></div><div class="skeleton skeleton-cell w-sm"></div><div class="skeleton skeleton-cell w-md"></div></div>`;
        }
        return html;
    }

    function showToast(msg, type = 'success') {
        const t = document.createElement('div');
        t.className = `toast toast-${type}`;
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 3500);
    }

    function todayISO() {
        return new Date().toISOString().split('T')[0];
    }

    // ================================================================
    // Currency list (shared across all modals)
    // ================================================================
    const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'DKK', 'SEK', 'NOK', 'HKD', 'SGD'];

    function populateCurrencySelects() {
        const opts = CURRENCIES.map(c => `<option value="${c}">${c}</option>`).join('');
        $$('.currency-select').forEach(el => { el.innerHTML = opts; });
    }

    // ================================================================
    // Table Sorting
    // ================================================================
    let sortState = {};

    function sortTable(tableContainerId, data, renderFn, column) {
        const prev = sortState[tableContainerId];
        let dir = 'asc';
        if (prev && prev.column === column) {
            dir = prev.dir === 'asc' ? 'desc' : 'asc';
        }
        sortState[tableContainerId] = { column, dir };

        const sorted = [...data].sort((a, b) => {
            let va = a[column], vb = b[column];
            if (va == null) va = '';
            if (vb == null) vb = '';
            if (typeof va === 'number' && typeof vb === 'number') {
                return dir === 'asc' ? va - vb : vb - va;
            }
            va = String(va).toLowerCase();
            vb = String(vb).toLowerCase();
            return dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        });
        renderFn(sorted);

        const container = document.getElementById(tableContainerId);
        if (container) {
            container.querySelectorAll('th.sortable').forEach(th => {
                th.classList.remove('sort-asc', 'sort-desc');
                if (th.dataset.sort === column) {
                    th.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
                }
            });
        }
    }

    // ================================================================
    // Tab Navigation
    // ================================================================
    const tabLoaded = {};
    const tabLoaders = {
        portfolio:    function () { loadSubTab('portfolio', 'holdings'); },
        intelligence: function () { loadSubTab('intelligence', 'events'); },
        alerts:    loadAlerts,
        audit:     loadAudit,
        command:   loadCommand,
        settings:  loadSettings,
    };

    // Sub-tab loaders map
    const subTabLoaders = {
        holdings:  loadHoldings,
        exposures: loadExposures,
        trades:    loadTrades,
        events:    loadEvents,
        analysis:  loadAnalysisNotes,
        digest:    loadDigest,
        inbox:     () => loadInbox(),
    };

    window.switchTab = switchTab;
    function switchTab(name) {
        // For primary tabs, update the nav
        $$('.tab-link').forEach(l => {
            const isActive = l.dataset.tab === name;
            l.classList.toggle('active', isActive);
            l.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        $$('.tab-panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + name));
        // Phase 9L: stop the operator-panel status poller whenever the
        // user leaves Settings, so we're not burning a poll every 4s
        // when the panel isn't visible.  Safe to call even when the
        // poller isn't running.
        if (name !== 'settings' && typeof _opStopStatusPolling === 'function') {
            _opStopStatusPolling();
        }
        if (!tabLoaded[name] && tabLoaders[name]) {
            tabLoaded[name] = true;
            tabLoaders[name]();
        }
    }

    function loadSubTab(parent, subtab) {
        // Switch sub-tab within a parent tab
        const parentEl = document.getElementById('tab-' + parent);
        if (!parentEl) return;
        parentEl.querySelectorAll('.sub-tab').forEach(b => {
            b.classList.toggle('active', b.dataset.subtab === subtab);
        });
        parentEl.querySelectorAll('.sub-panel').forEach(p => {
            p.classList.toggle('active', p.id === 'subtab-' + subtab);
        });
        // Load content if not already loaded
        if (!tabLoaded[subtab] && subTabLoaders[subtab]) {
            tabLoaded[subtab] = true;
            subTabLoaders[subtab]();
        }
    }

    function refreshTab(name) {
        tabLoaded[name] = false;
        const loader = tabLoaders[name] || subTabLoaders[name];
        if (loader && ($('.tab-link.active')?.dataset.tab === name || document.querySelector('.sub-tab.active[data-subtab="' + name + '"]'))) {
            tabLoaded[name] = true;
            loader();
        }
    }

    // ================================================================
    // Phase 9G — Portfolio Intelligence Overview (top of Holdings tab)
    // ================================================================
    const _POSTURE_STYLE = {
        strong_negative: { label: 'Elevated risk',        cls: 'posture-strong-negative', dot: 'red'    },
        mildly_negative: { label: 'Mildly negative',      cls: 'posture-mild-negative',   dot: 'orange' },
        mixed:           { label: 'Mixed',                cls: 'posture-mixed',           dot: 'yellow' },
        constructive:    { label: 'Constructive',         cls: 'posture-constructive',    dot: 'green'  },
        strong_positive: { label: 'Strongly constructive',cls: 'posture-strong-positive', dot: 'green'  },
        insufficient_data: { label: 'Not enough data',    cls: 'posture-insufficient',    dot: 'gray'   },
    };

    function _directionArrow(direction) {
        const d = (direction || '').toLowerCase();
        if (d === 'up')   return '&#8593;';  // ↑
        if (d === 'down') return '&#8595;';  // ↓
        if (d === 'unclear' || d === 'unknown' || d === '') return '&middot;';
        return esc(d);
    }

    function _freshnessChip(freshness) {
        if (!freshness || !freshness.last_event_fetched_at) {
            return `<span class="intel-chip intel-chip-stale" title="No news collected yet">No news yet</span>`;
        }
        const mins = freshness.stale_minutes;
        if (mins == null) {
            return `<span class="intel-chip intel-chip-stale" title="Unknown freshness">Freshness unknown</span>`;
        }
        const fresh = !!freshness.is_fresh;
        const label = mins < 60
            ? `${mins}m ago`
            : mins < 60 * 24
                ? `${Math.floor(mins / 60)}h ago`
                : `${Math.floor(mins / 1440)}d ago`;
        return `<span class="intel-chip ${fresh ? '' : 'intel-chip-stale'}" title="Last event collected">Updated ${label}</span>`;
    }

    function _alertBadge(alerts) {
        if (!alerts || !alerts.total) {
            return `<span class="intel-chip" title="No active alerts">&#9679; 0 alerts</span>`;
        }
        const parts = [];
        if (alerts.critical) parts.push(`<span class="sev-dot sev-dot-critical"></span>${alerts.critical} critical`);
        if (alerts.high)     parts.push(`<span class="sev-dot sev-dot-high"></span>${alerts.high} high`);
        if (alerts.warning)  parts.push(`<span class="sev-dot sev-dot-warning"></span>${alerts.warning} warn`);
        const cls = alerts.critical || alerts.high ? 'intel-chip intel-chip-alert' : 'intel-chip';
        const body = parts.length ? parts.join(' &middot; ') : `${alerts.total} active`;
        return `<span class="${cls}" title="Unacknowledged alerts">${body}</span>`;
    }

    // Phase 9N — action priority to CSS class mapping
    const _ACTION_PRIORITY_STYLE = {
        high:   { cls: 'intel-action-high',   label: 'High'   },
        medium: { cls: 'intel-action-medium', label: 'Medium' },
        low:    { cls: 'intel-action-low',    label: 'Low'    },
    };

    // Phase 9O — evidence ref categorisation (mirrors the backend
    // ``src/intelligence/traceability.group_evidence_refs`` vocabulary
    // so we don't round-trip for every action card).
    const _REF_CATEGORY_ORDER = [
        ['factor:',           'factors'],
        ['alert:',            'alerts'],
        ['rel:',              'relationships'],
        ['holding:',          'holdings'],
        ['ticker:',           'tickers'],
        ['related:',          'related'],
        ['note:',             'notes'],
        ['attention:',        'attention'],
        ['repeat_neg:',       'repeat_negative'],
        ['holdings:',         'counts'],
        ['distinct_factors=', 'counts'],
        ['stale_minutes=',    'freshness'],
        ['reconcile.',        'maintenance'],
        ['backfill.',         'maintenance'],
        ['manual_edit',       'maintenance'],
    ];
    const _REF_CATEGORY_LABEL = {
        factors: 'Factors', alerts: 'Alerts', relationships: 'Relationships',
        holdings: 'Holdings', tickers: 'Tickers', related: 'Related',
        notes: 'Notes', attention: 'Attention', repeat_negative: 'Repeat negatives',
        counts: 'Counts', freshness: 'Freshness', maintenance: 'Maintenance',
        other: 'Other',
    };

    function _groupEvidenceRefs(refs) {
        const out = {};
        for (const r of (refs || [])) {
            if (typeof r !== 'string' || !r) continue;
            let category = 'other';
            for (const [prefix, name] of _REF_CATEGORY_ORDER) {
                if (r.startsWith(prefix)) { category = name; break; }
            }
            (out[category] = out[category] || []).push(r);
        }
        return out;
    }

    function _renderEvidenceRefsChips(refs, { maxInline = 3, className = 'evidence-refs' } = {}) {
        // Compact one-line "Grounded in" chip row.  Used under
        // recommended action rows and on alert cards.  Truncates to
        // ``maxInline`` visible chips + a "+N more" indicator.
        const list = Array.isArray(refs) ? refs.filter(r => typeof r === 'string' && r) : [];
        if (!list.length) return '';
        const visible = list.slice(0, maxInline);
        const extra = list.length - visible.length;
        const chips = visible.map(r => `<span class="evidence-ref-chip" title="${esc(r)}">${esc(r)}</span>`).join('');
        const more = extra > 0 ? `<span class="evidence-ref-more" title="${esc(list.slice(maxInline).join(', '))}">+${extra} more</span>` : '';
        return `<div class="${esc(className)}"><span class="evidence-refs-label">Grounded in:</span>${chips}${more}</div>`;
    }

    //: Phase 9Q — render a single evidence ref chip, with a
    //: clickable affordance when a nav target is available.  Shared
    //: by both the flat and grouped evidence renderers so the two
    //: forms stay visually identical.
    function _renderSingleEvidenceChip(ref, navTarget) {
        if (navTarget && typeof navTarget === 'object' && navTarget.surface) {
            return `<button type="button" class="evidence-ref-chip evidence-ref-clickable" data-nav-jump="1" data-nav-target="${escAttr(JSON.stringify(navTarget))}" title="${esc(ref)}">${esc(ref)}</button>`;
        }
        return `<span class="evidence-ref-chip" title="${esc(ref)}">${esc(ref)}</span>`;
    }

    function _renderEvidenceRefsGrouped(refs, { heading = 'Grounded in', targets = null } = {}) {
        // Longer grouped form used by the event detail modal.  When
        // refs span multiple categories we render one line per
        // category; when they're all in one or two categories we fall
        // back to the compact flat chip row.
        //
        // Phase 9Q — an optional ``targets`` parallel array (same
        // ordering as ``refs``) turns chips into clickable deep
        // links when a nav target is present.
        const list = Array.isArray(refs) ? refs.filter(r => typeof r === 'string' && r) : [];
        if (!list.length) return '';
        // Build a ref→nav_target lookup from the parallel targets list
        const navByRef = {};
        if (Array.isArray(targets)) {
            for (const t of targets) {
                if (t && typeof t === 'object' && typeof t.ref === 'string') {
                    navByRef[t.ref] = t.nav_target || null;
                }
            }
        }
        const groups = _groupEvidenceRefs(list);
        const keys = Object.keys(groups);
        if (keys.length <= 1) {
            return `
                <div class="evidence-refs evidence-refs-grouped">
                    <span class="evidence-refs-label">${esc(heading)}:</span>
                    ${list.map(r => _renderSingleEvidenceChip(r, navByRef[r])).join('')}
                </div>`;
        }
        const rows = Object.entries(groups).map(([cat, refsInCat]) => `
            <div class="evidence-ref-group" data-category="${esc(cat)}">
                <span class="evidence-ref-group-label">${esc(_REF_CATEGORY_LABEL[cat] || cat)}:</span>
                ${refsInCat.map(r => _renderSingleEvidenceChip(r, navByRef[r])).join('')}
            </div>
        `).join('');
        return `
            <div class="evidence-refs evidence-refs-grouped">
                <span class="evidence-refs-label">${esc(heading)}:</span>
                <div class="evidence-ref-groups">${rows}</div>
            </div>`;
    }

    function _renderRecommendedActions(actions, hiddenCount) {
        // Compact "Recommended Actions" zone for the Phase 9G
        // intelligence overview card.  Up to 3 items, honest empty
        // state, grounded-only (every action comes from the Phase 9N
        // backend builder).
        //
        // Phase 9T — ``hiddenCount`` is the number of handled/dismissed
        // actions suppressed by the action-state filter.  The list
        // only contains VISIBLE actions.  Each action row carries a
        // dismiss button that calls the ``/api/v1/actions/set-state``
        // endpoint.
        const list = Array.isArray(actions) ? actions : [];
        const hc = typeof hiddenCount === 'number' ? hiddenCount : 0;
        if (!list.length && !hc) {
            return `
                <div class="intel-actions-block" id="intel-actions-zone">
                    <div class="intel-block-label">Recommended actions</div>
                    <div class="intel-actions-empty">
                        No immediate actions from current signals.
                    </div>
                </div>`;
        }
        if (!list.length && hc > 0) {
            return `
                <div class="intel-actions-block" id="intel-actions-zone">
                    <div class="intel-block-label">Recommended actions</div>
                    <div class="intel-actions-empty">
                        All actions handled.
                    </div>
                    <div class="intel-actions-hidden-footer text-xs text-muted">
                        ${hc} handled action${hc !== 1 ? 's' : ''} hidden
                    </div>
                </div>`;
        }
        const top = list.slice(0, 3);
        const items = top.map(a => {
            const style = _ACTION_PRIORITY_STYLE[a.priority] || _ACTION_PRIORITY_STYLE.low;
            const tickers = (a.related_tickers || []).slice(0, 4);
            const tickerPills = tickers.length
                ? `<div class="intel-action-tickers">${tickers.map(t => `<span class="ticker-badge">${esc(t)}</span>`).join('')}</div>`
                : '';
            const refsRow = _renderEvidenceRefsChips(a.rationale_refs, {
                maxInline: 3, className: 'intel-action-refs evidence-refs',
            });
            const nav = a.nav_target && typeof a.nav_target === 'object' && a.nav_target.surface
                ? a.nav_target
                : null;
            const navBtn = nav
                ? `<button type="button" class="intel-action-jump" data-nav-jump="1">${esc(nav.label || 'Open')} &rarr;</button>`
                : '';
            const navDataAttr = nav ? `data-nav-target="${escAttr(JSON.stringify(nav))}"` : '';
            // Phase 9T — dismiss button.  The fingerprint is stashed
            // on the row so the click handler can send it to the API.
            const fp = a.fingerprint || '';
            const dismissBtn = `<button type="button" class="btn btn-ghost btn-sm intel-action-dismiss" data-action-key="${esc(a.key || '')}" data-action-fp="${esc(fp)}" title="Dismiss this action">Dismiss</button>`;
            return `
                <div class="intel-action-row intel-action-${esc(a.priority || 'low')}" data-action-key="${esc(a.key || '')}" data-action-fp="${esc(fp)}" ${navDataAttr}>
                    <div class="intel-action-main">
                        <span class="intel-action-priority ${style.cls}">${esc(style.label)}</span>
                        <div class="intel-action-title">${esc(a.title || '')}</div>
                        <span class="intel-action-dismiss-spacer"></span>
                        ${dismissBtn}
                    </div>
                    <div class="intel-action-body">${esc(a.description || '')}</div>
                    ${tickerPills}
                    ${refsRow}
                    ${navBtn ? `<div class="intel-action-nav">${navBtn}</div>` : ''}
                </div>`;
        }).join('');
        const hiddenFooter = hc > 0
            ? `<div class="intel-actions-hidden-footer text-xs text-muted">${hc} handled action${hc !== 1 ? 's' : ''} hidden</div>`
            : '';
        return `
            <div class="intel-actions-block" id="intel-actions-zone">
                <div class="intel-block-label">Recommended actions</div>
                <div class="intel-actions-list">${items}</div>
                ${hiddenFooter}
            </div>`;
    }

    // Phase 9T — action state helpers (dismiss, read-all, refresh)
    async function _dismissAction(actionKey, fingerprint) {
        if (!actionKey) return;
        try {
            await postJSON(API.actionsSetState, {
                portfolio_id: _activePortfolioId,
                action_key: actionKey,
                state: 'dismissed',
                fingerprint: fingerprint || '',
            });
            // Refresh the overview to reflect the change
            if (typeof loadIntelligenceOverview === 'function') loadIntelligenceOverview();
            // Also refresh the inbox badge since action items may vanish
            if (typeof refreshInboxBadgeOnly === 'function') refreshInboxBadgeOnly();
        } catch (e) {
            if (typeof showToast === 'function') showToast('Dismiss failed: ' + e.message, 'error');
        }
    }

    // Click delegation for action dismiss buttons (inside the overview card)
    document.addEventListener('click', (ev) => {
        const dismissBtn = ev.target.closest('.intel-action-dismiss');
        if (dismissBtn) {
            ev.preventDefault();
            ev.stopPropagation();
            _dismissAction(
                dismissBtn.dataset.actionKey,
                dismissBtn.dataset.actionFp,
            );
            return;
        }
    });

    function renderIntelligenceOverview(data) {
        const el = $('#intelligence-overview');
        if (!el) return;
        if (!data) { el.innerHTML = ''; return; }

        const postureKey = data.posture || 'insufficient_data';
        const posture = _POSTURE_STYLE[postureKey] || _POSTURE_STYLE.insufficient_data;
        const name = data.portfolio_name || data.portfolio_id || 'Portfolio';

        const factors = (data.top_factors || []).slice(0, 4);
        const rels = (data.top_relationships || []).slice(0, 3);
        const attention = data.holdings_under_attention || [];
        // Phase 9T — use effective actions (pre-filtered by action state)
        // when available; fall back to the raw summary actions for
        // backward compat.
        const actions = data._effective_actions || data.recommended_actions || [];
        const hiddenCount = typeof data._hidden_action_count === 'number' ? data._hidden_action_count : 0;

        // Phase 9V — when factors come from classified MacroFactorEvents
        // (source='classified', no holdings), show event count instead
        // of holding count and use "observed" arrow style.
        const isClassifiedFactors = factors.length > 0 && factors[0].source === 'classified';
        const factorSectionLabel = isClassifiedFactors ? 'Observed macro signals' : 'Top factor pressures';
        const factorsHtml = factors.length
            ? factors.map(f => {
                const hasHoldings = (f.holdings || []).length > 0;
                const countLabel = hasHoldings
                    ? `${f.holdings.length} holding${f.holdings.length === 1 ? '' : 's'}`
                    : (f.event_count ? `${f.event_count} event${f.event_count === 1 ? '' : 's'}` : '');
                return `
                <span class="intel-factor-pill" title="${esc(f.factor)} max relevance ${(f.max_relevance ?? 0).toFixed(2)}">
                    <strong>${esc(f.label || f.factor)}</strong>
                    <span class="intel-factor-arrow">${_directionArrow(f.direction)}</span>
                    <span class="text-xs text-muted">${countLabel}</span>
                </span>`;
            }).join('')
            : `<span class="text-sm text-muted">No macro signals detected yet.</span>`;

        const relsHtml = rels.length
            ? rels.map(r => `
                <span class="intel-rel-pill" title="${esc(r.relationship_type)} link via ${esc(r.ticker)}">
                    <strong>${esc(r.ticker)}</strong>
                    <span class="intel-rel-arrow">&rarr;</span>
                    ${esc(r.related_entity || r.relationship_type)}
                    <span class="text-xs text-muted">(${esc(r.relationship_type)})</span>
                </span>`).join('')
            : `<span class="text-sm text-muted">No relationship touchpoints.</span>`;

        const attentionHtml = attention.length
            ? `<div class="intel-attention">
                   <span class="intel-attention-label">Needs attention:</span>
                   ${attention.slice(0, 6).map(t => `<span class="ticker-badge ticker-badge-warn">${esc(t)}</span>`).join('')}
               </div>`
            : '';

        const trustHealth = data.intelligence_health || {};
        const healthBits = [];
        if (trustHealth.factor_links != null)       healthBits.push(`${trustHealth.factor_links} factor links`);
        if (trustHealth.relationship_links != null) healthBits.push(`${trustHealth.relationship_links} relationship links`);
        if (trustHealth.analysis_notes_7d != null)  healthBits.push(`${trustHealth.analysis_notes_7d} analyses (7d)`);
        const healthLine = healthBits.length
            ? `<div class="intel-health" title="Deterministic intelligence artefacts">${healthBits.join(' &middot; ')}</div>`
            : '';

        const recentEvents = data.recent_events_count_24h || 0;

        el.innerHTML = `
            <div class="intel-overview-card">
                <div class="intel-overview-header">
                    <div class="intel-posture">
                        <span class="dot ${posture.dot}"></span>
                        <div>
                            <div class="intel-posture-label ${posture.cls}">${esc(posture.label)}</div>
                            <div class="intel-posture-reason" title="${esc(data.posture_reason || '')}">${esc(data.posture_reason || '')}</div>
                        </div>
                    </div>
                    <div class="intel-overview-meta">
                        <span class="text-sm text-muted">${esc(name)}</span>
                        ${_alertBadge(data.alerts)}
                        ${_freshnessChip(data.freshness)}
                        <span class="intel-chip" title="News items linked to your holdings in the last 24h">${recentEvents} news item${recentEvents === 1 ? '' : 's'} (24h)</span>
                    </div>
                </div>
                <div class="intel-overview-grid">
                    <div class="intel-block">
                        <div class="intel-block-label">${esc(factorSectionLabel)}</div>
                        <div class="intel-pill-row">${factorsHtml}</div>
                    </div>
                    <div class="intel-block">
                        <div class="intel-block-label">Relationship touchpoints</div>
                        <div class="intel-pill-row">${relsHtml}</div>
                    </div>
                </div>
                ${attentionHtml}
                ${_renderRecommendedActions(actions, hiddenCount)}
                ${healthLine}
            </div>`;
    }

    async function loadIntelligenceOverview() {
        const el = $('#intelligence-overview');
        if (!el) return;
        try {
            // Phase 9T — fetch summary + effective actions in parallel.
            // The effective-actions endpoint returns only VISIBLE
            // actions (filtered by the fingerprint-aware state table).
            const [data, effectiveResp] = await Promise.all([
                fetchJSON(_pq(API.intelligenceSummary)),
                fetchJSON(`${API.actionsEffective}?portfolio_id=${encodeURIComponent(_activePortfolioId)}`).catch(() => null),
            ]);
            if (data && effectiveResp && Array.isArray(effectiveResp.visible)) {
                data._effective_actions = effectiveResp.visible;
                data._hidden_action_count = effectiveResp.hidden_count || 0;
            }
            renderIntelligenceOverview(data);
        } catch (e) {
            // Non-fatal: the overview is a premium addition, not load-blocking.
            el.innerHTML = '';
        }
    }

    // ================================================================
    // Holdings Tab
    // ================================================================
    let allHoldings = [];

    async function loadHoldings() {
        const summaryEl = $('#holdings-summary');
        const tableEl = $('#holdings-table');
        if (!tableEl) { console.error('holdings-table element not found'); return; }
        // Kick off the premium intelligence overview in parallel — it's
        // portfolio-scoped and grounded in deterministic data only.
        loadIntelligenceOverview();
        // Show skeleton while loading (only on first load)
        if (!allHoldings.length && tableEl.querySelector('.spinner')) {
            tableEl.innerHTML = renderSkeleton(6);
        }
        try {
            const [summary, holdings, health] = await Promise.all([
                fetchJSON(_pq(API.summary)).catch(() => null),
                fetchJSON(_pq(API.holdings)).catch(() => []),
                fetchJSON(API.health).catch(() => null)
            ]);
            const list = ensureArray(holdings, 'items', 'holdings');
            allHoldings = list;
            window._lastHealthData = health;

            // Overview band — compact stats + system status in one row
            if (summaryEl) {
                if (list.length > 0) {
                    const tv = summary?.total_market_value ?? list.reduce((s, h) => s + (h.current_price || 0) * (h.quantity || 0), 0);
                    const hCount = summary?.holding_count ?? list.length;
                    const sCount = summary?.sector_count || new Set(list.map(h => h.sector).filter(Boolean)).size;

                    const lastColl = health?.last_collection ? timeAgo(health.last_collection) : null;
                    const srcHealthy = health?.sources_healthy ?? 0;
                    const srcActive = health?.sources_active ?? 0;
                    const srcDegraded = srcActive > 0 && srcHealthy < srcActive * 0.5;
                    const srcLabel = srcActive > 0 ? `${srcHealthy}/${srcActive} sources OK` : '';
                    const freshnessHtml = lastColl
                        ? `<span class="overview-freshness" title="Last news collection">Updated ${lastColl}</span>${srcLabel ? ` <span class="overview-freshness${srcDegraded ? ' overview-freshness-stale' : ''}" title="${srcHealthy} of ${srcActive} enabled sources returned data">&middot; ${srcLabel}</span>` : ''}`
                        : `<span class="overview-freshness overview-freshness-stale" title="No news collected yet">No data collected yet</span>`;

                    summaryEl.innerHTML = `<div class="overview-band">
                        <div class="overview-stat">
                            <div class="label">${esc((_portfolioList.find(p => p.id === _activePortfolioId) || {}).name || 'Portfolio')}</div>
                            <div class="value">${formatCurrency(tv)}</div>
                        </div>
                        <div class="overview-divider"></div>
                        <div class="overview-stat">
                            <div class="label">Holdings</div>
                            <div class="value value-sm">${hCount}</div>
                        </div>
                        <div class="overview-stat">
                            <div class="label">Sectors</div>
                            <div class="value value-sm">${sCount}</div>
                        </div>
                        <div class="overview-freshness-wrap">${freshnessHtml}</div>
                    </div>`;
                } else if (health) {
                    summaryEl.innerHTML = `<div class="overview-band">
                        <div class="overview-stat">
                            <div class="label">Portfolio Value</div>
                            <div class="value">\u2014</div>
                        </div>
                    </div>`;
                } else {
                    summaryEl.innerHTML = '';
                }
            }

            renderHoldingsTable(list);
        } catch (e) {
            tableEl.innerHTML = renderError('holdings: ' + e.message);
        }
    }

    function renderWelcomeCard(health) {
        const llmOk = health?.llm_available;
        const collected = !!health?.last_collection;
        const srcCount = health?.sources_active ?? 0;
        const sysOk = health?.status === 'ok';

        const check = '&#10003;';  // ✓
        const circle = '&#9675;';  // ○

        // Capabilities summary — show what's available right now
        // Phase 4 rewrites: no promise of live prices, no implication AI is
        // required, no implication sources are configured for the user.
        const coreFeatures = 'CSV portfolio import (offline), holdings + exposures, trade history, news collection from public RSS feeds, deterministic risk alerts, daily digests, automatic backup before every upgrade.';
        const aiFeatures = 'LLM-narrated impact scoring, conversational assistant over your data, AI vision extraction for scanned-PDF imports. Needs an Anthropic, OpenAI, or Google key.';

        return `<div class="welcome-card" data-first-run="empty">
            <div class="welcome-header">
                <div>
                    <div class="welcome-title">Welcome to Axion</div>
                    <div class="welcome-subtitle">Local portfolio intelligence. CSV import works offline, AI is optional, sources can be configured later.</div>
                </div>
            </div>
            <div class="welcome-steps">
                <div class="welcome-step ${sysOk ? 'done' : ''}">
                    <span class="step-icon">${sysOk ? check : circle}</span>
                    <div>
                        <div class="step-label">System running</div>
                        <div class="step-hint">${sysOk ? `${srcCount} source${srcCount === 1 ? '' : 's'} enabled` : 'Starting up\u2026'}</div>
                    </div>
                </div>
                <div class="welcome-step">
                    <span class="step-icon">${circle}</span>
                    <div>
                        <div class="step-label">Import your portfolio</div>
                        <div class="step-hint">CSV is the fastest path. PDF and image upload also work; scanned PDFs need an AI vision provider.</div>
                    </div>
                </div>
                <div class="welcome-step ${llmOk ? 'done' : ''}">
                    <span class="step-icon">${llmOk ? check : circle}</span>
                    <div>
                        <div class="step-label">Connect an AI provider <span class="text-xs text-muted">(optional)</span></div>
                        <div class="step-hint">${llmOk ? 'AI provider configured and reachable.' : 'All core features work without AI. Add a key in Settings to enable narrative analysis and the Assistant tab.'}</div>
                    </div>
                </div>
                <div class="welcome-step ${collected ? 'done' : ''}">
                    <span class="step-icon">${collected ? check : circle}</span>
                    <div>
                        <div class="step-label">First news collection</div>
                        <div class="step-hint">${collected ? 'News collected.' : 'Runs every 30 minutes automatically — nothing to do here.'}</div>
                    </div>
                </div>
            </div>
            <div class="welcome-capabilities">
                <div class="capabilities-summary">
                    <div class="capabilities-group">
                        <div class="capabilities-label"><span class="dot green"></span>Available now</div>
                        <div class="capabilities-text">${coreFeatures}</div>
                    </div>
                    <div class="capabilities-group">
                        <div class="capabilities-label"><span class="dot ${llmOk ? 'green' : 'yellow'}"></span>${llmOk ? 'AI features (active)' : 'With an AI key'}</div>
                        <div class="capabilities-text">${aiFeatures}</div>
                    </div>
                </div>
                <div class="welcome-note text-xs text-muted">
                    Want to try it without your real data? A sample CSV ships in <code>sample_portfolio.csv</code> at the project root — open it in Finder / File Explorer and drag onto the Import dialog.
                </div>
            </div>
            <div class="welcome-actions">
                <button class="btn btn-primary" onclick="uploadPortfolio()">Import portfolio</button>
                <button class="btn btn-outline" onclick="document.querySelector('[data-tab=settings]').click()">Configure AI / sources</button>
            </div>
        </div>`;
    }

    function renderHoldingsTable(list) {
        const el = $('#holdings-table');
        if (!list.length) {
            // Show the welcome card with setup guidance
            const healthData = window._lastHealthData || null;
            el.innerHTML = renderWelcomeCard(healthData);
            return;
        }
        const totalVal = list.reduce((s, h) => s + (h.market_value || (h.current_price || 0) * (h.quantity || 0)), 0);
        const enriched = list.map(h => {
            const mv = h.market_value || ((h.current_price || 0) * (h.quantity || 0)) || null;
            const pnl = h.pnl ?? (h.avg_cost_basis != null && h.current_price != null ? (h.current_price - h.avg_cost_basis) * (h.quantity || 0) : null);
            const pnl_pct = h.pnl_pct ?? (h.avg_cost_basis != null && h.avg_cost_basis !== 0 && h.current_price != null ? (h.current_price - h.avg_cost_basis) / h.avg_cost_basis : null);
            const wt = h.weight_pct ?? (totalVal > 0 && mv ? (mv / totalVal) * 100 : null);
            return { ...h, _mv: mv, _pnl: pnl, _pnl_pct: pnl_pct, _wt: wt };
        });
        el.innerHTML = `<div class="table-wrap"><table>
            <thead><tr>
                <th class="sortable" data-sort="ticker">Ticker</th><th class="sortable" data-sort="name">Name</th><th class="sortable" data-sort="sector">Sector</th>
                <th class="num sortable" data-sort="quantity">Shares</th><th class="num sortable" data-sort="avg_cost_basis">Avg Cost</th><th class="num sortable" data-sort="current_price">Price</th>
                <th class="num sortable" data-sort="_mv">Market Value</th><th class="num sortable" data-sort="_wt">Allocation</th><th class="num sortable" data-sort="_pnl">P&L</th><th class="num sortable" data-sort="_pnl_pct">P&L %</th>
                <th style="width:70px;"></th>
            </tr></thead>
            <tbody>${enriched.map(h => `<tr data-holding-id="${esc(h.id)}" data-ticker="${esc(h.ticker)}">
                <td><a href="javascript:void(0)" class="font-semibold text-mono" onclick="openHoldingDetail('${esc(h.id)}')" style="cursor:pointer">${esc(h.ticker)}</a></td>
                <td>${esc(h.name || h.company_name || '\u2014')}</td>
                <td>${h.sector ? `<span class="badge badge-muted">${esc(titleCase(h.sector))}</span>` : '<span class="text-muted">\u2014</span>'}</td>
                <td class="num">${formatNum(h.quantity, 0)}</td>
                <td class="num">${formatNum(h.avg_cost_basis)}</td>
                <td class="num">${formatNum(h.current_price)}</td>
                <td class="num">${formatCurrency(h._mv)}</td>
                <td class="num">${h._wt != null ? h._wt.toFixed(1) + '%' : '\u2014'}</td>
                <td class="num ${pnlClass(h._pnl)}">${formatCurrency(h._pnl)}</td>
                <td class="num ${pnlClass(h._pnl_pct)}">${formatPct(h._pnl_pct)}</td>
                <td>
                    <div class="row-actions">
                        <button class="btn-icon" onclick="openEditHolding('${esc(h.id)}')" title="Edit holding">&#9998;</button>
                        <button class="btn-icon btn-icon-danger" onclick="openDeleteHolding('${esc(h.id)}', '${esc(h.ticker)}')" title="Remove holding">&#10005;</button>
                    </div>
                </td>
            </tr>`).join('')}</tbody></table></div>`;
    }

    function filterHoldings(query) {
        const q = query.toLowerCase();
        const filtered = q ? allHoldings.filter(h =>
            (h.ticker || '').toLowerCase().includes(q) ||
            (h.name || '').toLowerCase().includes(q)
        ) : allHoldings;
        renderHoldingsTable(filtered);
    }

    // ================================================================
    // Add Holding
    // ================================================================
    window.openAddHolding = function () {
        const modal = $('#add-holding-modal');
        $('#ah-ticker').value = '';
        $('#ah-quantity').value = '';
        $('#ah-cost').value = '';
        $('#ah-price').value = '';
        $('#ah-currency').value = 'USD';
        $('#ah-isin').value = '';
        $('#ah-preview').innerHTML = '';
        $('#add-holding-title').textContent = 'Add Holding';
        $('#add-holding-btn').textContent = 'Add to Portfolio';
        $('#add-holding-btn').disabled = false;
        modal.showModal();
        $('#ah-ticker').focus();
    };

    // Live preview for add holding
    function updateAddPreview() {
        const qty = parseFloat($('#ah-quantity')?.value) || 0;
        const price = parseFloat($('#ah-price')?.value) || parseFloat($('#ah-cost')?.value) || 0;
        const el = $('#ah-preview');
        if (!el) return;
        if (qty > 0 && price > 0) {
            const mv = qty * price;
            const ccy = $('#ah-currency')?.value || 'USD';
            el.innerHTML = `Est. Market Value: <strong>${formatCurrency(mv, ccy)}</strong>`;
        } else {
            el.innerHTML = '';
        }
    }

    window.submitAddHolding = async function () {
        const ticker = $('#ah-ticker').value.trim().toUpperCase();
        const quantity = parseFloat($('#ah-quantity').value);

        if (!ticker) { showToast('Ticker is required', 'error'); return; }
        if (!quantity || quantity <= 0) { showToast('Quantity must be > 0', 'error'); return; }

        const data = {
            ticker,
            quantity,
            currency: $('#ah-currency').value || 'USD',
            portfolio_id: getActivePortfolioId(),
        };
        const cost = parseFloat($('#ah-cost').value);
        const price = parseFloat($('#ah-price').value);
        const isin = $('#ah-isin').value.trim();
        if (cost > 0) data.avg_cost_basis = cost;
        if (price > 0) data.current_price = price;
        if (isin) data.isin = isin;

        await withLoading($('#add-holding-btn'), 'Adding...', async () => {
            try {
                await postJSON(API.holdings, data);
                $('#add-holding-modal').close();
                showToast(`${ticker} added to portfolio`);
                refreshTab('holdings');
                refreshTab('exposures');
            } catch (e) {
                showToast('Could not add holding: ' + e.message, 'error');
            }
        });
    };

    // ================================================================
    // Edit Holding
    // ================================================================
    window.openEditHolding = function (holdingId) {
        const h = allHoldings.find(x => x.id === holdingId);
        if (!h) { showToast('Holding not found', 'error'); return; }

        const modal = $('#edit-holding-modal');
        $('#eh-id').value = h.id;
        $('#eh-ticker-display').textContent = h.ticker;
        $('#edit-holding-title').textContent = `Edit ${h.ticker}`;
        $('#eh-quantity').value = h.quantity ?? '';
        $('#eh-cost').value = h.avg_cost_basis ?? '';
        $('#eh-price').value = h.current_price ?? '';
        $('#eh-currency').value = h.currency || 'USD';
        $('#edit-holding-btn').disabled = false;
        $('#edit-holding-btn').textContent = 'Save Changes';
        modal.showModal();
    };

    window.submitEditHolding = async function () {
        const id = $('#eh-id').value;
        if (!id) return;

        const data = {};
        const qty = parseFloat($('#eh-quantity').value);
        const cost = parseFloat($('#eh-cost').value);
        const price = parseFloat($('#eh-price').value);
        const ccy = $('#eh-currency').value;

        if (!isNaN(qty) && qty > 0) data.quantity = qty;
        if (!isNaN(cost) && cost >= 0) data.avg_cost_basis = cost;
        if (!isNaN(price) && price >= 0) data.current_price = price;
        if (ccy) data.currency = ccy;

        if (Object.keys(data).length === 0) {
            showToast('No changes to save', 'error');
            return;
        }

        await withLoading($('#edit-holding-btn'), 'Saving...', async () => {
            try {
                await putJSON(API.holdingById(id), data);
                $('#edit-holding-modal').close();
                showToast('Holding updated');
                refreshTab('holdings');
                refreshTab('exposures');
            } catch (e) {
                showToast('Could not update holding: ' + e.message, 'error');
            }
        });
    };

    // ================================================================
    // Delete Holding
    // ================================================================
    window.openDeleteHolding = function (holdingId, ticker) {
        const modal = $('#delete-modal');
        $('#delete-id').value = holdingId;
        $('#delete-ticker').textContent = ticker;
        modal.showModal();
    };

    window.confirmDeleteHolding = async function () {
        const id = $('#delete-id').value;
        if (!id) return;

        await withLoading($('#delete-btn'), 'Removing...', async () => {
            try {
                await deleteJSON(API.holdingById(id));
                $('#delete-modal').close();
                showToast('Holding removed');
                refreshTab('holdings');
                refreshTab('exposures');
            } catch (e) {
                showToast('Could not remove holding: ' + e.message, 'error');
            }
        });
    };

    // ================================================================
    // Record Trade
    // ================================================================
    window.openRecordTrade = function (prefillTicker) {
        const modal = $('#trade-modal');
        $('#tr-ticker').value = prefillTicker || '';
        $('#tr-type').value = 'buy';
        $('#tr-quantity').value = '';
        $('#tr-price').value = '';
        $('#tr-date').value = todayISO();
        $('#tr-currency').value = 'USD';
        $('#tr-notes').value = '';
        $('#tr-preview').innerHTML = '';
        $('#trade-btn').disabled = false;
        $('#trade-btn').textContent = 'Submit Trade';
        modal.showModal();
        if (!prefillTicker) $('#tr-ticker').focus();
    };

    function updateTradePreview() {
        const qty = parseFloat($('#tr-quantity')?.value) || 0;
        const price = parseFloat($('#tr-price')?.value) || 0;
        const el = $('#tr-preview');
        if (!el) return;
        if (qty > 0 && price > 0) {
            const total = qty * price;
            const type = $('#tr-type')?.value || 'buy';
            const ccy = $('#tr-currency')?.value || 'USD';
            const label = type === 'buy' ? 'Total Cost' : type === 'sell' ? 'Total Proceeds' : 'Dividend Amount';
            el.innerHTML = `${label}: <strong>${formatCurrency(total, ccy)}</strong>`;
        } else {
            el.innerHTML = '';
        }
    }

    window.submitTrade = async function () {
        const ticker = $('#tr-ticker').value.trim().toUpperCase();
        const quantity = parseFloat($('#tr-quantity').value);
        const price = parseFloat($('#tr-price').value);
        const tradeDate = $('#tr-date').value;

        if (!ticker) { showToast('Ticker is required', 'error'); return; }
        if (!quantity || quantity <= 0) { showToast('Quantity must be > 0', 'error'); return; }
        if (isNaN(price) || price < 0) { showToast('Price is required', 'error'); return; }
        if (!tradeDate) { showToast('Trade date is required', 'error'); return; }

        const data = {
            ticker,
            trade_type: $('#tr-type').value,
            quantity,
            price,
            trade_date: tradeDate,
            currency: $('#tr-currency').value || 'USD',
        };
        const notes = $('#tr-notes').value.trim();
        if (notes) data.notes = notes;

        await withLoading($('#trade-btn'), 'Submitting...', async () => {
            try {
                await postJSON(API.trades, data);
                $('#trade-modal').close();
                const typeLabel = data.trade_type.charAt(0).toUpperCase() + data.trade_type.slice(1);
                showToast(`${typeLabel}: ${quantity} ${ticker} @ ${formatNum(price)}`);
                refreshTab('holdings');
                refreshTab('exposures');
            } catch (e) {
                showToast('Could not submit trade: ' + e.message, 'error');
            }
        });
    };

    // ================================================================
    // Exposures Tab
    // ================================================================
    async function loadExposures() {
        const container = $('#exposure-cards');
        const dims = ['sector', 'geography', 'currency', 'theme'];
        const labels = { sector: 'Sector', geography: 'Geography', currency: 'Currency', theme: 'Theme' };
        try {
            const results = await Promise.all(dims.map(d => fetchJSON(_pq(API.exposure(d))).catch(() => null)));
            container.innerHTML = dims.map((dim, i) => {
                const data = results[i];
                const buckets = data?.buckets || [];
                return `<div class="card">
                    <div class="card-header">${labels[dim]} Exposure</div>
                    ${buckets.length ? buckets.map(b => `
                        <div class="exposure-item">
                            <div class="exposure-label" title="${esc(b.label)}">${esc(titleCase(b.label))}</div>
                            <div class="exposure-track"><div class="exposure-fill" style="width:${Math.min(b.weight_pct || 0, 100)}%"></div></div>
                            <div class="exposure-value">${(b.weight_pct || 0).toFixed(1)}%</div>
                        </div>`).join('') : '<div class="empty-state" style="padding:1.5rem"><p class="text-sm">Upload a portfolio to see exposure breakdown.</p></div>'}
                </div>`;
            }).join('');
        } catch (e) {
            container.innerHTML = renderError('exposures: ' + e.message);
        }
    }

    // ================================================================
    // Trade History Sub-Tab
    // ================================================================
    let allTrades = [];

    async function loadTrades() {
        const el = $('#trades-table');
        try {
            const data = await fetchJSON(_pq(API.trades));
            const list = ensureArray(data, 'items', 'trades');
            allTrades = list;
            renderTradesTable(list);
        } catch (e) {
            el.innerHTML = renderError('trades: ' + e.message);
        }
    }

    function renderTradesTable(list) {
        const el = $('#trades-table');
        const q = ($('#trades-search') || {}).value || '';
        const filtered = q ? list.filter(t =>
            (t.ticker || '').toLowerCase().includes(q.toLowerCase()) ||
            (t.trade_type || '').toLowerCase().includes(q.toLowerCase()) ||
            (t.notes || '').toLowerCase().includes(q.toLowerCase())
        ) : list;

        if (!filtered.length) {
            el.innerHTML = renderEmpty('trades', 'No trades recorded yet.', {
                hint: 'Record buy, sell, or dividend trades to track your portfolio activity.',
                actions: [{ label: 'Record Trade', onclick: 'openRecordTrade()', primary: true }]
            });
            return;
        }

        const typeBadge = (t) => {
            const colors = { buy: 'var(--success)', sell: 'var(--danger)', dividend: '#a78bfa' };
            return `<span class="badge" style="background:${colors[t] || 'var(--muted-fg)'};color:#fff;font-size:0.65rem;">${esc((t || '').toUpperCase())}</span>`;
        };

        const rows = filtered.map(t => {
            const total = ((t.quantity || 0) * (t.price || 0)).toFixed(2);
            return `<tr>
                <td>${t.trade_date ? new Date(t.trade_date).toLocaleDateString() : '—'}</td>
                <td><strong>${esc(t.ticker)}</strong></td>
                <td>${typeBadge(t.trade_type)}</td>
                <td class="text-right">${Number(t.quantity).toLocaleString()}</td>
                <td class="text-right">${formatCurrency(t.price)}</td>
                <td class="text-right">${formatCurrency(parseFloat(total))}</td>
                <td class="text-xs text-muted">${esc(t.notes || '—')}</td>
            </tr>`;
        }).join('');

        el.innerHTML = `<div class="table-wrap"><table class="data-table">
            <thead><tr>
                <th>Date</th><th>Ticker</th><th>Type</th><th class="text-right">Quantity</th><th class="text-right">Price</th><th class="text-right">Total</th><th>Notes</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table></div>`;
    }

    document.addEventListener('DOMContentLoaded', () => {
        const trSearch = document.getElementById('trades-search');
        if (trSearch) trSearch.addEventListener('input', () => renderTradesTable(allTrades));
    });

    // ================================================================
    // Events Tab
    // ================================================================
    let allEvents = [];

    async function loadEvents() {
        const el = $('#events-table');
        if (!allEvents.length && el.querySelector('.spinner')) {
            el.innerHTML = renderSkeleton(4);
        }
        try {
            const data = await fetchJSON(API.events);
            const list = ensureArray(data, 'items', 'events');
            allEvents = list;
            renderEventsTable(list);
        } catch (e) {
            el.innerHTML = renderError('events: ' + e.message);
        }
    }

    function renderFactorTagsMini(tags) {
        if (!Array.isArray(tags) || !tags.length) return '';
        // Cap at 3 tags per row to keep the table tight
        const shown = tags.slice(0, 3);
        const overflow = tags.length - shown.length;
        const chips = shown.map(t => {
            const dir = (t.direction || 'unknown').toLowerCase();
            const arrow = dir === 'up' ? '\u2191' : dir === 'down' ? '\u2193' : '\u2022';
            return `<span class="factor-tag-mini direction-${esc(dir)}" title="${esc(t.label || t.key)} (${esc(t.direction || 'unknown')}, ${esc(t.magnitude || 'unknown')})">${arrow} ${esc(t.label || t.key)}</span>`;
        }).join('');
        const more = overflow > 0 ? `<span class="factor-tag-mini" title="${overflow} more">+${overflow}</span>` : '';
        return `<span class="factor-tag-list-mini">${chips}${more}</span>`;
    }

    function renderEventsTable(list) {
        const el = $('#events-table');
        if (!list.length) {
            el.innerHTML = renderEmpty('events', 'No news collected yet.', {
                hint: 'News items appear here as sources are polled. Collection runs automatically every 30 minutes; you can also trigger a manual run.',
                actions: [{ label: 'Run Collection', onclick: "runAction('collection')", primary: true }]
            });
            return;
        }
        el.innerHTML = `<div class="table-wrap"><table>
            <thead><tr>
                <th class="sortable" data-sort="title">Title</th>
                <th class="sortable" data-sort="event_type">Type</th>
                <th class="sortable" data-sort="materiality">Materiality</th>
                <th>Holdings</th>
                <th>Source</th>
                <th class="sortable" data-sort="published_at">Published</th>
            </tr></thead>
            <tbody>${list.map(e => {
                const tags = renderFactorTagsMini(e.factor_tags || []);
                const tickerCount = e.linked_ticker_count || 0;
                return `<tr class="events-row-clickable" data-event-id="${esc(e.id)}">
                    <td><span class="event-row-title">${esc(e.title || 'Untitled')}</span>${tags}</td>
                    <td>${e.event_type ? `<span class="badge badge-muted">${esc(titleCase(e.event_type))}</span>` : '<span class="text-muted">\u2014</span>'}</td>
                    <td>${e.materiality && e.materiality !== 'unscored' ? `<span class="badge badge-${e.materiality === 'critical' ? 'critical' : e.materiality === 'high' ? 'high' : 'info'}">${esc(e.materiality)}</span>` : '<span class="text-muted">unscored</span>'}</td>
                    <td class="text-sm text-muted">${tickerCount ? `${tickerCount} \uFF0F ${tickerCount === 1 ? 'holding' : 'holdings'}` : '<span class="text-muted">\u2014</span>'}</td>
                    <td class="text-sm text-muted">${esc(e.source_name || '\u2014')}</td>
                    <td class="text-sm text-muted">${formatDate(e.published_at)}</td>
                </tr>`;
            }).join('')}</tbody></table></div>`;
    }

    // ================================================================
    // Phase 9B — Event Detail Modal
    // ================================================================

    function _renderChainCard(chain) {
        if (!chain || typeof chain !== 'object') return '';
        const origin = chain.origin || 'unknown';
        const originLabel = ({
            deterministic_factor: 'Deterministic factor',
            direct_match: 'Direct match',
            llm_screen: 'LLM screen',
            unknown: 'Unknown'
        })[origin] || origin;

        // Flow: event → channel → holding → effect
        const flowSteps = [];
        flowSteps.push('<span class="chain-step">event</span>');
        if (chain.channel_label || chain.channel) {
            flowSteps.push('<span class="chain-arrow">\u2192</span>');
            flowSteps.push(`<span class="chain-step">${esc(chain.channel_label || chain.channel)}${chain.factor_direction ? ` ${chain.factor_direction === 'up' ? '\u2191' : chain.factor_direction === 'down' ? '\u2193' : ''}` : ''}</span>`);
        }
        if (chain.holding_ticker) {
            flowSteps.push('<span class="chain-arrow">\u2192</span>');
            flowSteps.push(`<span class="chain-step">${esc(chain.holding_ticker)}</span>`);
        }
        if (chain.effect_direction && chain.effect_direction !== 'unclear') {
            flowSteps.push('<span class="chain-arrow">\u2192</span>');
            flowSteps.push(`<span class="chain-step">${esc(chain.effect_direction)}</span>`);
        }

        const metrics = [];
        if (chain.effect_confidence != null) {
            const pct = Math.round(chain.effect_confidence * 100);
            metrics.push(`<span>Relevance: <strong>${pct}%</strong></span>`);
        }
        if (chain.factor_confidence != null) {
            const pct = Math.round(chain.factor_confidence * 100);
            metrics.push(`<span>Factor confidence: <strong>${pct}%</strong></span>`);
        }
        if (chain.sensitivity_value != null) {
            const sv = Number(chain.sensitivity_value).toFixed(2);
            const src = chain.sensitivity_source ? ` (${esc(chain.sensitivity_source)})` : '';
            metrics.push(`<span>Sensitivity: <strong>${sv}${src}</strong></span>`);
        }
        if (chain.factor_magnitude && chain.factor_magnitude !== 'unknown') {
            metrics.push(`<span>Magnitude: <strong>${esc(chain.factor_magnitude)}</strong></span>`);
        }

        const rationale = Array.isArray(chain.rationale) && chain.rationale.length
            ? `<ul class="chain-rationale">${chain.rationale.map(r => `<li>${esc(r)}</li>`).join('')}</ul>`
            : '';

        return `
            <div class="chain-card">
                <div class="chain-card-header">
                    <span class="chain-summary">${esc(chain.summary || '')}</span>
                    <span class="chain-origin-badge chain-origin-${esc(origin)}">${esc(originLabel)}</span>
                </div>
                <div class="chain-flow">${flowSteps.join('')}</div>
                ${metrics.length ? `<div class="chain-metrics">${metrics.join('')}</div>` : ''}
                ${rationale}
            </div>
        `;
    }

    function _renderFactorTags(tags) {
        if (!Array.isArray(tags) || !tags.length) {
            return '<span class="empty-inline">No deterministic factor tags for this event.</span>';
        }
        return `<div class="event-factor-tags">${tags.map(t => {
            const dir = (t.direction || 'unknown').toLowerCase();
            const arrow = dir === 'up' ? '\u2191' : dir === 'down' ? '\u2193' : '\u2022';
            const conf = t.confidence != null ? ` \u00B7 ${Math.round(t.confidence * 100)}%` : '';
            return `<span class="factor-tag direction-${esc(dir)}"><span class="factor-arrow">${arrow}</span> ${esc(t.label || t.key)} <span style="opacity:0.7">(${esc(t.magnitude || 'unknown')}${conf})</span></span>`;
        }).join('')}</div>`;
    }

    function _renderAffectedHoldings(rows) {
        if (!Array.isArray(rows) || !rows.length) {
            return '<span class="empty-inline">No portfolio holdings linked to this event.</span>';
        }
        return `<div class="affected-holdings-list">${rows.map(h => {
            const pidBadge = h.portfolio_id ? `<span class="pid" title="Portfolio">${esc(h.portfolio_id)}</span>` : '';
            const weight = h.weight_pct != null ? `${Number(h.weight_pct).toFixed(1)}%` : '\u2014';
            const rel = h.max_relevance != null ? `${Math.round(h.max_relevance * 100)}%` : '\u2014';
            const types = (h.link_types || []).map(t => `<span class="badge badge-muted" style="font-size:0.66rem;">${esc(t.replace(/_/g, ' '))}</span>`).join(' ');
            return `<div class="affected-holding-row">
                <div style="flex:1;">
                    <span class="ticker">${esc(h.ticker)}</span>
                    ${pidBadge}
                    <div style="margin-top:0.15rem;display:flex;gap:0.35rem;flex-wrap:wrap;">${types}</div>
                </div>
                <div class="text-sm text-muted" style="text-align:right;">
                    <div>Weight: <strong style="color:var(--foreground);">${weight}</strong></div>
                    <div>Relevance: <strong style="color:var(--foreground);">${rel}</strong></div>
                    ${h.sector ? `<div style="font-size:0.66rem;opacity:0.7;">${esc(h.sector)}</div>` : ''}
                </div>
            </div>`;
        }).join('')}</div>`;
    }

    function _renderRelatedAnalyses(rows) {
        if (!Array.isArray(rows) || !rows.length) {
            return '<span class="empty-inline">No analysis notes reference this event yet.</span>';
        }
        return rows.map(n => {
            const materiality = n.materiality ? ` \u00B7 <span class="badge badge-${n.materiality === 'critical' ? 'critical' : n.materiality === 'important' ? 'high' : 'info'}">${esc(n.materiality)}</span>` : '';
            const ticker = n.ticker ? `<strong class="text-mono">${esc(n.ticker)}</strong> \u00B7 ` : '';
            return `<div class="related-row">
                <div class="row-title">${ticker}${esc((n.note_type || '').replace(/_/g, ' '))}${materiality}</div>
                <div class="text-sm" style="margin-top:0.2rem;">${esc(n.summary || '')}</div>
                <div class="row-meta">${formatDate(n.created_at)}</div>
            </div>`;
        }).join('');
    }

    function _renderRelatedAlerts(rows) {
        if (!Array.isArray(rows) || !rows.length) {
            return '<span class="empty-inline">No alerts reference this event.</span>';
        }
        return rows.map(a => {
            const sev = (a.severity || 'info').toLowerCase();
            const ack = a.acknowledged ? '<span class="badge badge-muted">acknowledged</span>' : '';
            return `<div class="related-row">
                <div class="row-title">${severityBadge(a.severity)} ${esc(a.title || '')} ${ack}</div>
                <div class="row-meta">${esc(a.alert_type || '')}${a.portfolio_id ? ` \u00B7 portfolio ${esc(a.portfolio_id)}` : ''} \u00B7 ${formatDate(a.created_at)}</div>
            </div>`;
        }).join('');
    }

    function _renderEventDetail(detail) {
        const metaBits = [];
        if (detail.event_type) metaBits.push(`<span><strong>Type:</strong> ${esc(titleCase(detail.event_type))}</span>`);
        if (detail.source_name) metaBits.push(`<span><strong>Source:</strong> ${esc(detail.source_name)}</span>`);
        if (detail.published_at) metaBits.push(`<span><strong>Published:</strong> ${formatDate(detail.published_at)}</span>`);
        if (detail.materiality && detail.materiality !== 'unscored') metaBits.push(`<span><strong>Materiality:</strong> ${esc(detail.materiality)}</span>`);

        const summaryBlock = detail.summary
            ? `<div class="event-detail-summary">${esc(detail.summary)}</div>`
            : '<span class="empty-inline">No summary available.</span>';

        const urlLink = detail.url
            ? `<div style="margin-top:0.5rem;"><a href="${esc(detail.url)}" target="_blank" rel="noopener" class="text-sm">Open source \u2197</a></div>`
            : '';

        const chains = (detail.links || [])
            .map(l => l.chain)
            .filter(Boolean);

        // Phase 9N — grounded explanation block (why it matters +
        // suggested action).  Only rendered when the backend
        // produced honest evidence; otherwise the block is silent.
        const why = detail.why_it_matters;
        const action = detail.suggested_action;
        // Phase 9O — group the grounded refs by category (factors,
        // holdings, notes, etc.) for a cleaner readback.
        // Phase 9Q — pass through the parallel ``explanation_grounded_in_targets``
        // so known-navigable refs render as clickable buttons.
        const groundedRefs = (detail.explanation_grounded_in || []);
        const groundedTargets = detail.explanation_grounded_in_targets || null;
        const groundedBlock = groundedRefs.length
            ? _renderEvidenceRefsGrouped(groundedRefs, {
                  heading: 'Grounded in',
                  targets: groundedTargets,
              })
            : '';
        const explanationBlock = (why || action || groundedRefs.length) ? `
            <div class="event-detail-group event-why-block">
                <h4>Why Axion flagged this</h4>
                ${why ? `<p class="event-why-text">${esc(why)}</p>` : ''}
                ${action ? `<div class="event-why-action"><strong>Suggested next step:</strong> ${esc(action)}</div>` : ''}
                ${groundedBlock}
            </div>
        ` : '';

        return `
            <div class="event-detail-group">
                <div class="event-detail-meta">${metaBits.join('')}</div>
                ${summaryBlock}
                ${urlLink}
            </div>
            ${explanationBlock}
            <div class="event-detail-group">
                <h4>Factor Tags</h4>
                ${_renderFactorTags(detail.factor_tags)}
            </div>
            <div class="event-detail-group">
                <h4>Affected Holdings</h4>
                ${_renderAffectedHoldings(detail.affected_holdings)}
            </div>
            <div class="event-detail-group">
                <h4>Causal Chains (${chains.length})</h4>
                ${chains.length
                    ? chains.map(_renderChainCard).join('')
                    : '<span class="empty-inline">No causal chains for this event.</span>'}
            </div>
            <div class="event-detail-group">
                <h4>Related Analyses</h4>
                ${_renderRelatedAnalyses(detail.related_analyses)}
            </div>
            <div class="event-detail-group">
                <h4>Related Alerts</h4>
                ${_renderRelatedAlerts(detail.related_alerts)}
            </div>
        `;
    }

    window.openEventDetail = async function (eventId) {
        if (!eventId) return;
        const modal = $('#event-detail-modal');
        const body = $('#event-detail-body');
        const titleEl = $('#event-detail-title');
        if (!modal || !body) return;
        body.innerHTML = '<div class="spinner">Loading event detail...</div>';
        if (titleEl) titleEl.textContent = 'News item';
        modal.showModal();

        // Phase 9R — wire the copy-link button to the current event.
        const copyBtn = $('#event-detail-copy-link');
        if (copyBtn) {
            copyBtn.onclick = () => {
                _copyDeepLink({
                    surface: 'events',
                    portfolio_id: _activePortfolioId,
                    entity_type: 'event',
                    entity_id: eventId,
                    subtab: 'events',
                    open_modal: true,
                    highlight_key: 'event:' + eventId,
                });
            };
        }

        try {
            const data = await fetchJSON(
                API.eventById(eventId) + '?portfolio_id=' + encodeURIComponent(_activePortfolioId)
            );
            // fetchJSON returns null on 404 — handle that as a clean
            // "not found" state instead of crashing on `data.title`.
            if (!data) {
                if (titleEl) titleEl.textContent = 'News item not found';
                body.innerHTML = '<span class="empty-inline">This event no longer exists or could not be loaded.</span>';
                return;
            }
            if (titleEl) titleEl.textContent = data.title || 'News item';
            body.innerHTML = _renderEventDetail(data);
        } catch (e) {
            body.innerHTML = renderError('event detail: ' + (e && e.message ? e.message : 'unknown error'));
        }
    };

    function filterEvents(query) {
        const q = query.toLowerCase();
        const filtered = q ? allEvents.filter(e =>
            (e.title || '').toLowerCase().includes(q) ||
            (e.event_type || '').toLowerCase().includes(q)
        ) : allEvents;
        renderEventsTable(filtered);
    }

    // ================================================================
    // Analysis Notes Tab
    // ================================================================
    async function loadAnalysisNotes(ticker) {
        const el = $('#analysis-table');
        el.innerHTML = '<div class="spinner">Loading analysis notes...</div>';
        try {
            let url = _pq(API.analysisNotes);
            if (ticker) url += `&ticker=${ticker}`;
            const data = await fetchJSON(url);
            const list = ensureArray(data, 'items', 'notes');

            const filterEl = $('#analysis-filter');
            if (filterEl && !ticker) {
                const tickers = [...new Set(list.map(n => {
                    try { const c = JSON.parse(n.content || '{}'); return c.ticker || ''; } catch { return ''; }
                }).filter(Boolean))].sort();
                filterEl.innerHTML = '<option value="">All tickers</option>' + tickers.map(t =>
                    `<option value="${esc(t)}">${esc(t)}</option>`
                ).join('');
            }

            if (!list.length) {
                el.innerHTML = renderEmpty('analysis', 'No analysis notes yet.', {
                    hint: 'Analysis is generated from collected news once you have holdings to score against. AI-narrated analysis additionally requires an AI provider in Settings; deterministic rule-based scoring runs without one.',
                    actions: [{ label: 'Run Analysis', onclick: "runAction('analysis')", primary: true }]
                });
                return;
            }
            el.innerHTML = `<div class="table-wrap"><table>
                <thead><tr>
                    <th>Ticker</th><th>Impact</th><th>Magnitude</th>
                    <th>Outlook</th><th>Confidence</th><th>Date</th>
                </tr></thead>
                <tbody>${list.map(n => {
                    let content = {};
                    try { content = JSON.parse(n.content || '{}'); } catch {}
                    const direction = content.impact_direction || 'neutral';
                    const dirClass = direction === 'positive' ? 'text-success' : direction === 'negative' ? 'text-danger' : 'text-muted';
                    return `<tr>
                        <td><span class="font-semibold text-mono">${esc(content.ticker || '\u2014')}</span></td>
                        <td><span class="${dirClass} font-medium">${esc(direction)}</span></td>
                        <td>${content.impact_magnitude ? `<span class="badge badge-${content.impact_magnitude === 'high' ? 'high' : content.impact_magnitude === 'medium' ? 'warning' : 'info'}">${esc(content.impact_magnitude)}</span>` : '\u2014'}</td>
                        <td class="text-sm" style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(content.short_term_outlook || '')}">${esc(content.short_term_outlook || '\u2014')}</td>
                        <td>${n.confidence != null ? `<span class="confidence-bar"><span class="bar"><span class="bar-fill" style="width:${Math.round(parseFloat(n.confidence) * 100)}%"></span></span>${Math.round(parseFloat(n.confidence) * 100)}%</span>` : '\u2014'}</td>
                        <td class="text-sm text-muted">${formatDate(n.created_at)}</td>
                    </tr>`;
                }).join('')}</tbody></table></div>`;
        } catch (e) {
            el.innerHTML = renderError('analysis: ' + e.message);
        }
    }

    // ================================================================
    // Digest Tab
    // ================================================================
    function formatDigestContent(raw) {
        if (!raw || raw === '[]' || raw === '{}') return '<span class="text-muted">No data available.</span>';
        let parsed;
        try { parsed = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { return esc(raw); }
        if (Array.isArray(parsed)) {
            if (!parsed.length) return '<span class="text-muted">No data available.</span>';
            return '<ul>' + parsed.map(item => {
                if (typeof item === 'object' && item !== null) {
                    const title = item.title || item.name || '';
                    const body = item.body || item.content || item.summary || '';
                    const severity = item.severity ? `<span class="badge badge-${item.severity === 'critical' ? 'danger' : item.severity === 'high' ? 'warning' : 'muted'}">${esc(item.severity)}</span> ` : '';
                    return `<li>${severity}<strong>${esc(title)}</strong>${body ? ': ' + esc(body) : ''}</li>`;
                }
                return `<li>${esc(String(item))}</li>`;
            }).join('') + '</ul>';
        }
        if (typeof parsed === 'object' && parsed !== null) {
            return '<ul>' + Object.entries(parsed).map(([k, v]) =>
                `<li><strong>${esc(k.replace(/_/g, ' '))}:</strong> ${esc(String(v))}</li>`
            ).join('') + '</ul>';
        }
        return esc(String(parsed));
    }

    // Phase 9G: premium digest reader.  Reads the Phase 9E grounded
    // JSON shape directly (headline, portfolio_assessment, risk_flags,
    // holdings_requiring_attention, key_developments, factor_touchpoints)
    // and renders it as a skim-friendly hierarchy.  Falls back to the
    // legacy sections shape if the body doesn't look grounded.
    function _parseGroundedDigest(data) {
        if (!data) return null;
        // The API route flattens the inner JSON into `sections` where
        // each section carries the ORIGINAL key as title and the
        // stringified value as content.  We rehydrate the Phase 9E
        // shape from that flat form.
        const out = {};
        for (const s of (data.sections || [])) {
            const key = (s.title || '').toLowerCase().replace(/\s+/g, '_');
            let value = s.content;
            if (typeof value === 'string') {
                try { value = JSON.parse(value); } catch (_e) { /* keep as string */ }
            }
            out[key] = value;
        }
        return out;
    }

    function _renderGroundedDigest(data, grounded) {
        const headline = grounded.headline || data.summary || data.content || 'Intelligence digest';
        const assessment = grounded.portfolio_assessment || '';
        const risk_flags = Array.isArray(grounded.risk_flags) ? grounded.risk_flags : [];
        const attention = Array.isArray(grounded.holdings_requiring_attention) ? grounded.holdings_requiring_attention : [];
        const key_devs = Array.isArray(grounded.key_developments) ? grounded.key_developments : [];
        const market = grounded.market_context || '';
        const action_items = Array.isArray(grounded.action_items) ? grounded.action_items : [];

        const riskHtml = risk_flags.length
            ? `<div class="card digest-card digest-risk-flags">
                   <h3>Risk flags</h3>
                   <ul class="digest-list">
                       ${risk_flags.map(f => `<li>${esc(String(f))}</li>`).join('')}
                   </ul>
               </div>`
            : '';

        const attentionHtml = attention.length
            ? `<div class="card digest-card digest-attention">
                   <h3>Holdings needing attention</h3>
                   <div class="digest-attention-row">
                       ${attention.map(t => `<span class="ticker-badge ticker-badge-warn">${esc(String(t))}</span>`).join('')}
                   </div>
               </div>`
            : '';

        const developmentsHtml = key_devs.length
            ? `<div class="card digest-card digest-developments">
                   <h3>Key developments</h3>
                   <ul class="digest-list">
                       ${key_devs.map(d => `<li>${esc(String(d))}</li>`).join('')}
                   </ul>
               </div>`
            : '';

        const actionsHtml = action_items.length
            ? `<div class="card digest-card digest-actions">
                   <h3>Suggested actions</h3>
                   <ul class="digest-list">
                       ${action_items.map(a => `<li>${esc(String(a))}</li>`).join('')}
                   </ul>
               </div>`
            : '';

        const marketHtml = (market && !market.toLowerCase().includes('deterministic fallback'))
            ? `<div class="card digest-card digest-market">
                   <h3>Market context</h3>
                   <p class="digest-content">${esc(market)}</p>
               </div>`
            : '';

        // Phase 9O — compact trust footer.  The digest already
        // carries ``event_count``, ``alert_count``, and
        // ``holding_count`` from the backend; we just render them as
        // a single grounded line so operators can see at a glance
        // what this digest was compiled from.
        const trustBits = [];
        if (data.event_count != null && data.event_count > 0) {
            trustBits.push(`${data.event_count} event${data.event_count === 1 ? '' : 's'}`);
        }
        if (data.alert_count != null && data.alert_count > 0) {
            trustBits.push(`${data.alert_count} alert${data.alert_count === 1 ? '' : 's'}`);
        }
        if (data.holding_count != null && data.holding_count > 0) {
            trustBits.push(`${data.holding_count} holding${data.holding_count === 1 ? '' : 's'}`);
        }
        const trustHtml = trustBits.length
            ? `<div class="digest-trust-footer">
                   <span class="digest-trust-label">Based on</span>
                   <span class="digest-trust-body">${trustBits.join(' · ')}</span>
                   <span class="digest-trust-hint text-xs text-muted">from current signals</span>
               </div>`
            : '';

        return `
            <div class="digest-meta">
                <span class="badge badge-primary">${esc(data.digest_type || 'daily')}</span>
                <span class="text-sm text-muted">${formatDateShort(data.period_start)} — ${formatDateShort(data.period_end)}</span>
                ${data.event_count != null ? `<span class="badge badge-info">${data.event_count} events</span>` : ''}
                ${data.alert_count != null ? `<span class="badge badge-warning">${data.alert_count} alerts</span>` : ''}
            </div>
            <div class="card digest-card digest-headline">
                <h2 class="digest-headline-text">${esc(String(headline))}</h2>
                ${assessment ? `<p class="digest-content">${esc(String(assessment))}</p>` : ''}
            </div>
            ${riskHtml}
            ${attentionHtml}
            ${developmentsHtml}
            ${actionsHtml}
            ${marketHtml}
            ${trustHtml}`;
    }

    function _hasGroundedFields(grounded) {
        if (!grounded) return false;
        return (
            grounded.headline != null
            || grounded.portfolio_assessment != null
            || Array.isArray(grounded.risk_flags)
            || Array.isArray(grounded.holdings_requiring_attention)
            || Array.isArray(grounded.key_developments)
        );
    }

    async function loadDigest() {
        const el = $('#digest-content');
        try {
            const data = await fetchJSON(_pq(API.digestLatest));
            if (!data) {
                el.innerHTML = renderEmpty('digest', 'No digest generated yet.', {
                    hint: 'A digest summarises portfolio activity, alerts, and material news. You can generate one after some news has been collected; with an AI key it also produces a short narrative.',
                    actions: [{ label: 'Generate Digest', onclick: 'generateDigest()', primary: true }]
                });
                return;
            }
            const grounded = _parseGroundedDigest(data);
            if (_hasGroundedFields(grounded)) {
                el.innerHTML = _renderGroundedDigest(data, grounded);
                return;
            }
            // Legacy fallback — preserve the pre-9G renderer for any
            // unknown / old digest shapes so older rows still display.
            const sections = data.sections || [];
            el.innerHTML = `
                <div class="digest-meta">
                    <span class="badge badge-primary">${esc(data.digest_type || 'daily')}</span>
                    <span class="text-sm text-muted">${formatDateShort(data.period_start)} — ${formatDateShort(data.period_end)}</span>
                    ${data.event_count != null ? `<span class="badge badge-info">${data.event_count} events</span>` : ''}
                    ${data.alert_count != null ? `<span class="badge badge-warning">${data.alert_count} alerts</span>` : ''}
                </div>
                ${sections.map(s => `
                    <div class="card mb-3 digest-section">
                        <h3>${esc(s.title)}</h3>
                        <div class="digest-content">${formatDigestContent(s.content)}</div>
                    </div>`).join('')}
                ${!sections.length ? `<div class="card"><div class="digest-content">${esc(data.summary || data.content || 'No content available.')}</div></div>` : ''}`;
        } catch (e) {
            el.innerHTML = renderError('digest: ' + e.message);
        }
    }

    // ================================================================
    // Alerts Tab
    // ================================================================

    // Phase 9W — severity filter mapping for server-side queries.
    // The ``list_alerts`` route accepts a single ``severity`` string
    // but our UI has compound options like "critical_high".  We expand
    // these to multiple calls or use the broader single-value match.
    // For simplicity, compound filters are expanded client-side after
    // a "no severity filter" server fetch (compound values don't map
    // 1-to-1 to the backend's single-value severity param).
    function _filterAlertsBySeverity(alerts, filterVal) {
        if (!filterVal || filterVal === 'all') return alerts;
        const sets = {
            'critical':      new Set(['critical']),
            'critical_high': new Set(['critical', 'high']),
            'high':          new Set(['critical', 'high']),
            'warning':       new Set(['critical', 'high', 'warning', 'medium']),
            'info':          new Set(['info', 'low']),
        };
        const allowed = sets[filterVal];
        if (!allowed) return alerts;
        return alerts.filter(a => allowed.has((a.severity || '').toLowerCase()));
    }

    async function loadAlerts() {
        const el = $('#alerts-content');
        try {
            // Phase 9W — server-backed alerts filtering.  Use the
            // ``list_alerts`` route (``/api/v1/alerts``) which already
            // supports ``severity`` + ``acknowledged`` query params,
            // instead of the old ``/active`` route that was hard-coded
            // to unacknowledged-only.
            const sevFilterVal = document.querySelector('#alerts-severity-filter')?.value || '';
            const ackFilterVal = document.querySelector('#alerts-ack-filter')?.value || '';

            // Build the URL with server-side params
            const params = new URLSearchParams({
                portfolio_id: _activePortfolioId,
                limit: '200',
            });
            // Map ack filter to the backend's ``acknowledged`` param
            if (ackFilterVal === 'open') {
                params.set('acknowledged', 'false');
            } else if (ackFilterVal === 'ack') {
                params.set('acknowledged', 'true');
            }
            // For single-value severity (critical, info), pass it
            // to the backend directly.  For compound values
            // (critical_high, warning), we'll filter client-side.
            const singleSeverities = new Set(['critical', 'info']);
            if (sevFilterVal && singleSeverities.has(sevFilterVal)) {
                params.set('severity', sevFilterVal);
            }

            const data = await fetchJSON(`${API.alerts}?${params.toString()}`);
            let list = ensureArray(data, 'items', 'alerts');

            // Apply client-side severity filter for compound values
            // that don't map to a single backend severity param.
            if (sevFilterVal && !singleSeverities.has(sevFilterVal)) {
                list = _filterAlertsBySeverity(list, sevFilterVal);
            }

            // Maintain severity-first ordering (Phase 9G)
            const sevRank = {critical:0,high:1,warning:2,medium:2,info:3,low:3};
            list.sort((a, b) => {
                const ra = sevRank[(a.severity||'').toLowerCase()] ?? 9;
                const rb = sevRank[(b.severity||'').toLowerCase()] ?? 9;
                if (ra !== rb) return ra - rb;
                return (b.created_at || '').localeCompare(a.created_at || '');
            });
            if (!list.length) {
                el.innerHTML = renderEmpty('check', 'No active alerts.', {
                    hint: 'This means no current rule-based alerts (concentration, calendar clusters, stale data, material news). It does not mean risk is zero — only that none of the configured rules have fired.'
                });
                return;
            }
            el.innerHTML = list.map(a => {
                // Phase 9N — grounded per-alert next-step hint.  The
                // backend fills ``suggested_action`` from the same
                // pure helper shared by the intelligence summary;
                // we just render it when present.
                const suggestedAction = a.suggested_action
                    ? `<div class="alert-suggested-action"><span class="alert-suggested-label">Next step:</span> ${esc(a.suggested_action)}</div>`
                    : '';
                // Phase 9Q — clickable evidence chips.  When the
                // backend provides ``evidence_targets`` (parallel list
                // of structured navigation targets), render each chip
                // as a button that calls jumpToTarget; otherwise fall
                // back to the Phase 9O static chip rendering.
                const evidenceTargets = Array.isArray(a.evidence_targets) ? a.evidence_targets : [];
                const evidenceChips = evidenceTargets.length
                    ? `<div class="alert-evidence-refs evidence-refs">
                           <span class="evidence-refs-label">Based on:</span>
                           ${evidenceTargets.slice(0, 4).map(et => {
                                const ref = et && et.ref || '';
                                const nav = et && et.nav_target;
                                if (nav) {
                                    return `<button type="button" class="evidence-ref-chip evidence-ref-clickable" data-nav-jump="1" data-nav-target="${escAttr(JSON.stringify(nav))}" title="${esc(ref)}">${esc(ref)}</button>`;
                                }
                                return `<span class="evidence-ref-chip" title="${esc(ref)}">${esc(ref)}</span>`;
                            }).join('')}
                       </div>`
                    : '';
                return `
                <div class="alert-card severity-${(a.severity || 'info').toLowerCase()}" data-alert-id="${esc(a.id)}">
                    <div class="flex justify-between items-center gap-2">
                        <div class="alert-title">${severityBadge(a.severity)} ${esc(titleCase(a.title))}</div>
                        ${!a.acknowledged ? `<button class="btn btn-sm btn-ghost btn-ack" data-alert-id="${esc(a.id)}">Acknowledge</button>` : ''}
                    </div>
                    <div class="text-sm text-muted mt-2">${esc(a.message?.replace(/_/g, ' '))}</div>
                    ${suggestedAction}
                    ${evidenceChips}
                    <div class="alert-meta">
                        <span class="text-xs text-muted">${timeAgo(a.created_at)}</span>
                        ${(a.related_holdings || []).length ? `<div class="alert-tickers">${a.related_holdings.map(hid => {
                            const h = allHoldings.find(x => x.id === hid);
                            return h ? `<span class="ticker-badge">${esc(h.ticker)}</span>` : '';
                        }).filter(Boolean).join('')}</div>` : ''}
                    </div>
                </div>`;
            }).join('');
        } catch (e) {
            el.innerHTML = renderError('alerts: ' + e.message);
        }
    }

    // ================================================================
    // Phase 9P — Notification Inbox
    // ================================================================
    //
    // Reads /api/v1/notifications?portfolio_id=<active>, renders a
    // sorted list of notification cards, wires up per-item mark-read,
    // and drives a small unread badge on the Inbox sub-tab button.
    // Triggered via subTabLoaders.inbox AND refreshed by the websocket
    // dispatcher when a relevant alert / operator_action / event
    // arrives.

    const _INBOX_SOURCE_STYLE = {
        alert:    { label: 'alert',    cls: 'inbox-src-alert'    },
        digest:   { label: 'digest',   cls: 'inbox-src-digest'   },
        operator: { label: 'operator', cls: 'inbox-src-operator' },
        action:   { label: 'action',   cls: 'inbox-src-action'   },
        event:    { label: 'event',    cls: 'inbox-src-event'    },
    };

    const _INBOX_PRIORITY_STYLE = {
        high:   { cls: 'inbox-priority-high',   label: 'High'   },
        medium: { cls: 'inbox-priority-medium', label: 'Medium' },
        low:    { cls: 'inbox-priority-low',    label: 'Low'    },
    };

    // ================================================================
    // Phase 9Q — Deep-link navigation engine
    // ================================================================
    //
    // Single centralized dispatcher for all structured navigation
    // targets produced by `src/intelligence/navigation.py`.  Every
    // clickable "jump" affordance (inbox, recommended actions,
    // evidence chips, operator recent actions) calls
    // ``jumpToTarget(target)`` instead of hard-coding the surface.

    //: Map a ``target.surface`` to the top-level tab name the dashboard
    //: uses.  The operator target lands under Settings.
    const _TARGET_SURFACE_TO_TAB = {
        alerts:    'alerts',
        digest:    'intelligence',
        events:    'intelligence',
        operator:  'settings',
        portfolio: 'portfolio',
    };

    function _inboxJumpDescriptor(item) {
        // Derive a compact "does this item have a jump?" descriptor
        // from the Phase 9Q structured target.  Returns ``null`` if
        // the backend didn't attach a target.
        const t = item && item.action_target;
        if (!t || typeof t !== 'object') return null;
        if (!t.surface || !_TARGET_SURFACE_TO_TAB[t.surface]) return null;
        return { target: t, label: t.label || item.action_label || 'Open' };
    }

    //: Surfaces that accept a sub-tab hint via ``loadSubTab``.  Only
    //: Intelligence has sub-tabs today — if the target is operator,
    //: we scroll to the matching anchor inside Settings instead.
    const _SURFACES_WITH_SUBTABS = new Set(['intelligence']);

    //: Lightweight temporary flash class applied via ``_highlightRowOnce``.
    //: The CSS animation lasts ~1.8s and then the class removes itself.
    const _NAV_HIGHLIGHT_CLASS = 'nav-highlight-flash';
    const _NAV_HIGHLIGHT_MS = 1800;

    function _highlightRowOnce(element) {
        if (!element) return;
        try {
            element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        } catch (_e) {
            // scrollIntoView isn't supported on detached / test nodes
        }
        element.classList.add(_NAV_HIGHLIGHT_CLASS);
        setTimeout(() => {
            try { element.classList.remove(_NAV_HIGHLIGHT_CLASS); }
            catch (_e) { /* node gone */ }
        }, _NAV_HIGHLIGHT_MS);
    }

    function _applyHighlight(target) {
        // Map a highlight_key to a DOM node and flash it briefly.
        // Keys use "<prefix>:<id>" form, mirroring the backend's
        // navigation.target_for_* output.
        //
        // Phase 9R — extended with exact holding, factor-row, and
        // relationship-row anchors:
        //   alert:<id>          → [data-alert-id='<id>']
        //   event:<id>          → [data-event-id='<id>']
        //   audit:<id>          → [data-audit-id='<id>']
        //   holding:<id>        → tr[data-holding-id='<id>']
        //   ticker:<sym>        → tr[data-ticker='<sym>']
        //   factor-row:<hid>:<f>→ tr[data-holding-id='<hid>'][data-factor='<f>']
        //   rel-row:<rel_id>    → tr[data-rel-id='<rel_id>']
        if (!target || !target.highlight_key) return;
        const key = target.highlight_key;
        const idx = key.indexOf(':');
        if (idx < 0) return;
        const prefix = key.slice(0, idx);
        const rest = key.slice(idx + 1);
        if (!prefix || !rest) return;
        let selector = null;
        if (prefix === 'alert') selector = `[data-alert-id="${CSS.escape(rest)}"]`;
        else if (prefix === 'event') selector = `[data-event-id="${CSS.escape(rest)}"]`;
        else if (prefix === 'audit') selector = `[data-audit-id="${CSS.escape(rest)}"]`;
        else if (prefix === 'holding') selector = `tr[data-holding-id="${CSS.escape(rest)}"]`;
        else if (prefix === 'ticker') selector = `tr[data-ticker="${CSS.escape(rest)}"]`;
        else if (prefix === 'factor-row') {
            // Format: factor-row:<holding_id>:<factor>
            const sep = rest.indexOf(':');
            if (sep >= 0) {
                const hid = rest.slice(0, sep);
                const fac = rest.slice(sep + 1);
                selector = `tr[data-holding-id="${CSS.escape(hid)}"][data-factor="${CSS.escape(fac)}"]`;
            }
        } else if (prefix === 'rel-row') {
            selector = `tr[data-rel-id="${CSS.escape(rest)}"]`;
        }
        else return;
        // Defer to next tick so the destination surface has finished rendering
        setTimeout(() => {
            const el = document.querySelector(selector);
            if (el) _highlightRowOnce(el);
        }, 350);
    }

    function _applyMaintenanceAnchor(target) {
        // Phase 9S — scroll + flash the exact reconcile or backfill
        // action block inside the Maintenance Actions card.  The
        // entity_type field carries the action name from the operator
        // entry shaper (``holding_relationships`` for reconcile,
        // ``intelligence_backfill`` for backfill).
        const actionMap = {
            'holding_relationships': 'reconcile',
            'intelligence_backfill': 'backfill',
        };
        const actionName = actionMap[target.entity_type] || null;
        if (!actionName) return;
        setTimeout(() => {
            const block = document.querySelector(
                `[data-maintenance-action="${actionName}"]`,
            );
            if (block) _highlightRowOnce(block);
        }, 400);
    }

    function _applyTargetFilter(target) {
        // Phase 9U — apply filter hints from either the new ``filters``
        // dict or the legacy single ``filter`` field.  Each surface
        // is handled explicitly so unknown filter keys are a no-op.
        if (!target) return;

        // Build a merged filters map (new ``filters`` dict takes
        // precedence; legacy ``filter`` is a fallback for the
        // operator-factor surface).
        const f = (target.filters && typeof target.filters === 'object')
            ? { ...target.filters }
            : {};
        // Legacy compat: if ``filter`` is set but ``filters.factor``
        // is not, promote it into the filters map for the factor
        // surface.
        if (target.filter && !f.factor && target.surface === 'operator' && target.subtab === 'factors') {
            f.factor = target.filter;
        }

        // --- Operator factor table filter ---
        if (target.surface === 'operator' && target.subtab === 'factors' && f.factor) {
            const sel = document.querySelector('#op-factor-filter');
            if (sel && sel.value !== f.factor) {
                const hasOption = Array.from(sel.options).some(o => o.value === f.factor);
                if (hasOption) {
                    sel.value = f.factor;
                    if (typeof loadOperatorFactorSensitivities === 'function') loadOperatorFactorSensitivities();
                }
            }
        }

        // --- Operator relationship source filter ---
        if (target.surface === 'operator' && target.subtab === 'relationships' && f.source) {
            const sel = document.querySelector('#op-rel-source-filter');
            if (sel && sel.value !== f.source) {
                const hasOption = Array.from(sel.options).some(o => o.value === f.source);
                if (hasOption) {
                    sel.value = f.source;
                    if (typeof loadOperatorRelationships === 'function') loadOperatorRelationships();
                }
            }
        }

        // --- Alerts severity + acknowledged filters ---
        if (target.surface === 'alerts') {
            let needsReload = false;
            if (f.severity) {
                const sel = document.querySelector('#alerts-severity-filter');
                if (sel) {
                    const hasOption = Array.from(sel.options).some(o => o.value === f.severity);
                    if (hasOption && sel.value !== f.severity) {
                        sel.value = f.severity;
                        needsReload = true;
                    }
                }
            }
            if (f.ack) {
                const sel = document.querySelector('#alerts-ack-filter');
                if (sel) {
                    const hasOption = Array.from(sel.options).some(o => o.value === f.ack);
                    if (hasOption && sel.value !== f.ack) {
                        sel.value = f.ack;
                        needsReload = true;
                    }
                }
            }
            if (needsReload && typeof loadAlerts === 'function') {
                tabLoaded.alerts = true;
                loadAlerts();
            }
        }

        // --- Events search ---
        if ((target.surface === 'events' || target.subtab === 'events') && f.search) {
            const input = document.querySelector('#events-search');
            if (input) {
                input.value = f.search;
                if (typeof filterEvents === 'function') filterEvents(f.search);
            }
        }
    }

    async function _switchPortfolioIfNeeded(target) {
        if (!target || !target.portfolio_id) return false;
        if (target.portfolio_id === _activePortfolioId) return false;
        // Portfolio mismatch — make sure the target portfolio exists
        // in the user's list before switching.  If not, we silently
        // skip the switch and let the jump land on the current one
        // (fail-safe; no cross-portfolio leakage).
        const known = _portfolioList.some(p => p.id === target.portfolio_id);
        if (!known) return false;
        // Use the same side-effecty switch helper the selector uses.
        const sel = document.querySelector('#portfolio-select');
        if (sel) sel.value = target.portfolio_id;
        window.switchPortfolio(target.portfolio_id);
        return true;
    }

    window.jumpToTarget = async function jumpToTarget(target) {
        // Single entry point for all Phase 9Q deep-link navigation.
        // Safe to call with any value — a null / malformed target is
        // a no-op, not a crash.
        if (!target || typeof target !== 'object') return;
        if (!target.surface || !_TARGET_SURFACE_TO_TAB[target.surface]) return;

        // 1) Portfolio first — never surface another portfolio's data
        await _switchPortfolioIfNeeded(target);

        // 2) Switch to the top-level tab
        const tab = _TARGET_SURFACE_TO_TAB[target.surface];
        if (typeof switchTab === 'function') {
            switchTab(tab);
        }

        // 3) Sub-tab hint (Intelligence → Events/Digest/etc.)
        if (target.subtab && _SURFACES_WITH_SUBTABS.has(tab)) {
            if (typeof loadSubTab === 'function') {
                loadSubTab(tab, target.subtab);
            }
        }

        // 4) Filter hint (Operator factor filter, etc.)
        // Deferred so the destination panel has a chance to mount.
        setTimeout(() => _applyTargetFilter(target), 200);

        // 5) Modal / detail open
        if (target.open_modal && target.entity_id) {
            if (target.entity_type === 'event' && typeof window.openEventDetail === 'function') {
                setTimeout(() => window.openEventDetail(target.entity_id), 300);
            }
            // Phase 9S — holding detail slide-out
            if (target.entity_type === 'holding' && typeof window.openHoldingDetail === 'function') {
                setTimeout(() => window.openHoldingDetail(target.entity_id), 350);
            }
        }

        // 6) Highlight / scroll-into-view for the focused row
        _applyHighlight(target);

        // Phase 9S — exact maintenance sub-anchor scroll
        if (target.surface === 'operator' && target.subtab === 'maintenance' && target.entity_type) {
            _applyMaintenanceAnchor(target);
        }

        // 7) Phase 9R — write the target to the URL hash so the link
        //    is shareable / reload-stable.  Deferred so the navigation
        //    side-effects finish before the address bar updates.
        setTimeout(() => _writeNavTargetToHash(target), 50);
    };

    // ================================================================
    // Phase 9R — Shareable Deep Links (URL hash lifecycle)
    // ================================================================

    const _NAV_HASH_PREFIX = 'nav=';

    function _encodeNavTarget(target) {
        // Encode a structured target dict → URL-safe base64 JSON hash.
        // Mirrors ``encode_nav_hash`` in ``src/intelligence/navigation.py``.
        if (!target || typeof target !== 'object') return '';
        const d = { ...target };
        delete d.label;  // strip UI-only field
        // Strip null/undefined values for compactness
        for (const k of Object.keys(d)) {
            if (d[k] === null || d[k] === undefined) delete d[k];
        }
        const json = JSON.stringify(d);
        // Base64url encode (no native btoa in Node but browsers have it)
        const b64 = btoa(unescape(encodeURIComponent(json)))
            .replace(/\+/g, '-')
            .replace(/\//g, '_')
            .replace(/=+$/, '');
        return '#' + _NAV_HASH_PREFIX + b64;
    }

    function _decodeNavTargetFromHash() {
        // Parse the current location.hash into a target dict.
        // Returns null if the hash is empty, malformed, or not a
        // nav-target.  Mirrors ``decode_nav_hash`` in navigation.py.
        const raw = location.hash ? location.hash.slice(1) : '';
        if (!raw.startsWith(_NAV_HASH_PREFIX)) return null;
        const b64 = raw.slice(_NAV_HASH_PREFIX.length);
        if (!b64) return null;
        // Re-add base64 padding
        const padded = b64 + '==='.slice(0, (4 - (b64.length % 4)) % 4);
        // Base64url → standard base64
        const std = padded.replace(/-/g, '+').replace(/_/g, '/');
        let json;
        try {
            json = decodeURIComponent(escape(atob(std)));
        } catch (_e) {
            return null;
        }
        let d;
        try {
            d = JSON.parse(json);
        } catch (_e) {
            return null;
        }
        if (!d || typeof d !== 'object' || !d.surface || !d.portfolio_id) return null;
        if (!_TARGET_SURFACE_TO_TAB[d.surface]) return null;
        return d;
    }

    // Phase 9S — history-aware navigation.  Track whether the current
    // jump was initiated by a popstate event (browser back/forward) so
    // we can suppress the history push and avoid an infinite loop.
    let _navIsPopstateReplay = false;

    function _writeNavTargetToHash(target) {
        const h = _encodeNavTarget(target);
        if (!h) return;
        try {
            if (_navIsPopstateReplay) {
                // During back/forward replay we do NOT push a new entry —
                // the browser already moved the cursor.  We replaceState
                // so the hash stays visually consistent if the user
                // later copies the URL.
                history.replaceState(null, '', h);
            } else {
                // Normal jump — push a NEW history entry so the user can
                // press back to return to the previous surface.
                history.pushState(null, '', h);
            }
        } catch (_e) { /* sandboxed — ignore */ }
    }

    function _clearNavHash() {
        try {
            history.replaceState(null, '', location.pathname + location.search);
        } catch (_e) { /* sandboxed — ignore */ }
    }

    // Phase 9S — popstate listener for browser back/forward.  When the
    // user presses back/forward, the browser fires ``popstate`` and
    // ``location.hash`` already reflects the target entry.  We decode
    // and replay the jump with the recursive-push guard so the history
    // cursor doesn't drift.
    window.addEventListener('popstate', () => {
        const target = _decodeNavTargetFromHash();
        if (!target) {
            // Back to a hashless URL → return to the default Portfolio tab.
            if (typeof switchTab === 'function') switchTab('portfolio');
            return;
        }
        _navIsPopstateReplay = true;
        try {
            if (typeof window.jumpToTarget === 'function') {
                window.jumpToTarget(target);
            }
        } finally {
            // Reset the flag AFTER the sync part of jumpToTarget finishes.
            // The async parts (portfolio switch, deferred highlight) are
            // fire-and-forget — they never push history because the flag
            // is still true when their setTimeout callbacks fire.  We
            // reset it on the NEXT macrotask so all deferred callbacks
            // inside jumpToTarget see the flag as true.
            setTimeout(() => { _navIsPopstateReplay = false; }, 500);
        }
    });

    //: Consume the URL hash on initial page load (consume-once model).
    //: If a valid nav target is found, execute the jump pipeline.
    //: The jump pushes a new history entry via pushState so the initial
    //: hashless state becomes the back-target.
    let _initialNavConsumed = false;
    function _consumeInitialNavHash() {
        if (_initialNavConsumed) return;
        _initialNavConsumed = true;
        const target = _decodeNavTargetFromHash();
        if (!target) return;
        // Defer the jump slightly so tab loaders from the default
        // bootstrap have a chance to mount DOM elements.
        setTimeout(() => {
            if (typeof window.jumpToTarget === 'function') {
                window.jumpToTarget(target);
            }
        }, 150);
    }

    // Expose for testing
    window._encodeNavTarget = _encodeNavTarget;
    window._decodeNavTargetFromHash = _decodeNavTargetFromHash;

    // ================================================================
    // Phase 9R — Copy-link affordance
    // ================================================================

    async function _copyDeepLink(target) {
        // Build the full absolute URL with the nav hash and copy it
        // to the clipboard.  Shows a lightweight toast on success.
        if (!target || typeof target !== 'object') return;
        const hash = _encodeNavTarget(target);
        if (!hash) return;
        const url = location.origin + location.pathname + hash;
        try {
            await navigator.clipboard.writeText(url);
            if (typeof showToast === 'function') {
                showToast('Deep link copied to clipboard');
            }
        } catch (_e) {
            // Clipboard write failed (sandboxed iframe, permissions) —
            // fall back to a simple prompt the user can Ctrl+C from.
            try { prompt('Copy this link:', url); }
            catch (_ee) { /* headless / test — ignore */ }
        }
    }

    // Expose for testing + inline onclick handlers
    window._copyDeepLink = _copyDeepLink;

    function _updateInboxBadge(summary) {
        // Drives the unread counter next to the Inbox sub-tab label.
        const badge = $('#inbox-unread-badge');
        if (!badge) return;
        const unread = (summary && typeof summary.unread === 'number') ? summary.unread : 0;
        if (unread > 0) {
            badge.textContent = String(unread > 99 ? '99+' : unread);
            badge.hidden = false;
        } else {
            badge.textContent = '0';
            badge.hidden = true;
        }
    }

    function _renderInboxItems(items) {
        const list = Array.isArray(items) ? items : [];
        if (!list.length) {
            return `<div class="inbox-empty card"><p class="text-sm text-muted">No notifications yet.</p><p class="text-xs text-muted">The inbox collects alerts, digests, operator actions, and high-priority recommendations. New items will appear here as they're generated.</p></div>`;
        }
        return `<ul class="inbox-list">${list.map(item => {
            const src = _INBOX_SOURCE_STYLE[item.source_type] || { label: item.source_type || 'item', cls: '' };
            const pri = _INBOX_PRIORITY_STYLE[item.priority] || _INBOX_PRIORITY_STYLE.low;
            const refs = (item.evidence_refs || []).slice(0, 3);
            const refsRow = refs.length
                ? `<div class="evidence-refs inbox-evidence-refs">
                       <span class="evidence-refs-label">Grounded in:</span>
                       ${refs.map(r => `<span class="evidence-ref-chip" title="${esc(r)}">${esc(r)}</span>`).join('')}
                   </div>`
                : '';
            const jump = _inboxJumpDescriptor(item);
            const jumpBtn = jump
                ? `<button class="btn btn-ghost btn-sm inbox-jump-btn" data-nav-jump="1">${esc(jump.label)} &rarr;</button>`
                : '';
            // Phase 9Q — stash the structured target on the row so a
            // single click delegator can invoke jumpToTarget without
            // re-parsing strings.  JSON-safe, escaped via esc().
            const targetJson = jump ? JSON.stringify(jump.target) : '';
            const readBtn = item.unread
                ? `<button class="btn btn-ghost btn-sm inbox-mark-read-btn" data-inbox-key="${esc(item.key)}" title="Mark read">Mark read</button>`
                : `<span class="inbox-read-chip text-xs text-muted" title="Marked read">&#10003; Read</span>`;
            // Phase 9R — compact copy-link button for shareable deep links
            const copyBtn = jump
                ? `<button type="button" class="btn btn-ghost btn-sm copy-link-btn inbox-copy-link-btn" title="Copy deep link" data-copy-target="${escAttr(targetJson)}">&#128279;</button>`
                : '';
            return `
                <li class="inbox-item ${item.unread ? 'inbox-item-unread' : 'inbox-item-read'} inbox-priority-${esc(item.priority || 'low')}" data-inbox-key="${esc(item.key)}" data-source-type="${esc(item.source_type || '')}" ${targetJson ? `data-nav-target="${escAttr(targetJson)}"` : ''}>
                    <div class="inbox-item-header">
                        <span class="inbox-source-badge ${src.cls}">${esc(src.label)}</span>
                        <span class="inbox-priority-pill ${pri.cls}">${esc(pri.label)}</span>
                        <span class="inbox-timestamp text-xs text-muted">${timeAgo(item.timestamp) || formatDate(item.timestamp)}</span>
                        <span class="inbox-spacer"></span>
                        ${copyBtn}
                        ${readBtn}
                    </div>
                    <div class="inbox-item-title">${esc(item.title || '')}</div>
                    ${item.body ? `<div class="inbox-item-body text-sm text-muted">${esc(item.body)}</div>` : ''}
                    ${refsRow}
                    ${jumpBtn ? `<div class="inbox-item-actions">${jumpBtn}</div>` : ''}
                </li>
            `;
        }).join('')}</ul>`;
    }

    function _renderInboxSummary(summary) {
        if (!summary) return '';
        const parts = [];
        if (summary.unread != null) {
            parts.push(`<span class="inbox-summary-unread"><strong>${summary.unread}</strong> unread</span>`);
        }
        if (summary.total != null) {
            parts.push(`<span class="inbox-summary-total text-xs text-muted">${summary.total} total</span>`);
        }
        const bs = summary.by_source || {};
        const bySrcParts = Object.keys(bs).map(k => `${bs[k]} ${k}`).join(' · ');
        if (bySrcParts) {
            parts.push(`<span class="inbox-summary-bysource text-xs text-muted">${esc(bySrcParts)}</span>`);
        }
        return `<div class="inbox-summary">${parts.join(' ')}</div>`;
    }

    async function loadInbox() {
        const el = $('#inbox-content');
        if (!el) return;
        const pid = encodeURIComponent(_activePortfolioId);
        try {
            const data = await fetchJSON(`${API.notificationsInbox}?portfolio_id=${pid}`);
            const items = (data && Array.isArray(data.items)) ? data.items : [];
            const summary = (data && data.summary) || { total: 0, unread: 0 };
            el.innerHTML = _renderInboxSummary(summary) + _renderInboxItems(items);
            _updateInboxBadge(summary);
        } catch (e) {
            el.innerHTML = `<div class="card"><p class="text-sm text-danger">Could not load inbox: ${esc(e.message || 'unknown error')}</p></div>`;
        }
    }

    async function markInboxItemRead(key) {
        if (!key) return;
        try {
            await postJSON(API.notificationsMarkRead, {
                key,
                portfolio_id: _activePortfolioId,
            });
            await loadInbox();
        } catch (e) {
            showToast('Mark read failed: ' + e.message, 'error');
        }
    }

    async function markAllInboxRead() {
        try {
            const resp = await postJSON(API.notificationsMarkAllRead, {
                portfolio_id: _activePortfolioId,
            });
            const n = (resp && typeof resp.marked === 'number') ? resp.marked : 0;
            showToast(n > 0 ? `Marked ${n} notification${n === 1 ? '' : 's'} read` : 'Nothing to mark read');
            await loadInbox();
        } catch (e) {
            showToast('Mark all read failed: ' + e.message, 'error');
        }
    }

    async function refreshInboxBadgeOnly() {
        // Lightweight header-only refresh used when the inbox panel is
        // not currently visible.  Hits the same endpoint but only
        // updates the unread counter so the sub-tab badge stays in
        // sync without rebuilding the list.
        const pid = encodeURIComponent(_activePortfolioId);
        try {
            const data = await fetchJSON(`${API.notificationsInbox}?portfolio_id=${pid}`);
            _updateInboxBadge(data && data.summary);
        } catch (_e) {
            // Silent — the badge just stays stale until the panel
            // reloads.  No toast; this runs on background events.
        }
    }

    // Phase 9Q — single click delegator for mark-read + deep-link
    // jumps.  A ``data-nav-target`` JSON attribute on any ancestor
    // element carries the structured target; a ``data-nav-jump="1"``
    // or ``.evidence-ref-chip[data-nav-jump]`` child triggers the
    // dispatcher.  This replaces the Phase 9P string-parsing version.
    function _dispatchNavFromEvent(ev) {
        // Phase 9R — copy-link buttons
        const copyBtn = ev.target.closest('.copy-link-btn[data-copy-target]');
        if (copyBtn) {
            ev.preventDefault();
            ev.stopPropagation();
            try {
                const target = JSON.parse(copyBtn.getAttribute('data-copy-target'));
                _copyDeepLink(target);
            } catch (_e) { /* malformed — ignore */ }
            return true;
        }
        const markBtn = ev.target.closest('.inbox-mark-read-btn');
        if (markBtn) {
            ev.preventDefault();
            ev.stopPropagation();
            markInboxItemRead(markBtn.dataset.inboxKey);
            return true;
        }
        const jumpBtn = ev.target.closest('[data-nav-jump="1"]');
        if (jumpBtn) {
            ev.preventDefault();
            ev.stopPropagation();
            // Walk up to find the nearest element carrying the target
            const host = jumpBtn.closest('[data-nav-target]') || jumpBtn;
            const raw = host.getAttribute('data-nav-target');
            if (!raw) return true;
            let target;
            try {
                target = JSON.parse(raw);
            } catch (_e) {
                return true;  // malformed — silently ignore
            }
            if (typeof window.jumpToTarget === 'function') {
                window.jumpToTarget(target);
            }
            return true;
        }
        return false;
    }

    document.addEventListener('click', _dispatchNavFromEvent);

    // Keyboard activation for role="button" elements (operator
    // recent rows, clickable evidence chips).  Mirrors the standard
    // behaviour browsers give to actual <button> elements.
    document.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        const target = ev.target.closest('[data-nav-jump="1"]');
        if (!target) return;
        // Avoid double-firing if the element is a native button/link
        // (the browser already emits a click event for Space/Enter).
        if (target.tagName === 'BUTTON' || target.tagName === 'A') return;
        _dispatchNavFromEvent(ev);
    });

    // ================================================================
    // Audit Tab
    // ================================================================
    async function loadAudit(entityType) {
        const el = $('#audit-content');
        el.innerHTML = '<div class="spinner">Loading audit trail...</div>';
        try {
            let url = API.audit;
            if (entityType) url += `?entity_type=${entityType}`;
            const data = await fetchJSON(url);
            const list = ensureArray(data, 'items', 'entries');
            if (!list.length) {
                el.innerHTML = renderEmpty(null, 'No audit entries found.');
                return;
            }
            el.innerHTML = `<div class="table-wrap"><table>
                <thead><tr><th>Time</th><th>Entity</th><th>ID</th><th>Action</th><th>Agent</th><th>Reason</th></tr></thead>
                <tbody>${list.map(e => `<tr>
                    <td class="text-sm text-muted">${formatDate(e.created_at)}</td>
                    <td><span class="badge badge-muted">${esc(e.entity_type)}</span></td>
                    <td class="text-mono text-sm">${esc(e.entity_id)}</td>
                    <td>${esc(e.action)}</td>
                    <td class="text-sm">${esc(e.agent_id || e.user_id || '\u2014')}</td>
                    <td class="text-sm text-muted">${esc(e.reason || '\u2014')}</td>
                </tr>`).join('')}</tbody></table></div>`;
        } catch (e) {
            el.innerHTML = renderError('audit: ' + e.message);
        }
    }

    // ================================================================
    // Health Tab
    // ================================================================
    async function loadHealth() {
        const healthEl = $('#health-content');
        const agentEl = $('#agent-status');
        const sourceEl = $('#source-health');
        try {
            const [health, agents, sources] = await Promise.all([
                fetchJSON(API.health),
                fetchJSON(API.agentStatus).catch(() => null),
                fetchJSON(API.sources).catch(() => null),
            ]);

            if (health) {
                // Update Telegram status from the same health response (avoids duplicate fetch)
                updateTelegramStatus(health);
                const st = (health.status || 'unknown').toLowerCase();
                const stLabel = st === 'ok' || st === 'healthy' ? 'Operational' : st.charAt(0).toUpperCase() + st.slice(1);
                healthEl.innerHTML = `<div class="card mb-3">
                    <div class="flex items-center gap-2 mb-3">
                        ${statusDot(st)}
                        <span class="font-semibold" style="font-size:1.1rem;">${esc(stLabel)}</span>
                    </div>
                    <div class="health-grid">
                        <div class="health-item"><div class="label">Database</div><div class="value">${statusDot(health.database || 'ok')} ${esc(health.database || 'connected')}</div></div>
                        <div class="health-item"><div class="label">Scheduler</div><div class="value">${statusDot(health.scheduler || 'ok')} ${esc(health.scheduler || 'running')}</div></div>
                        <div class="health-item"><div class="label">Sources</div><div class="value">${health.sources_active ?? '?'} / ${health.sources_total ?? '?'} active</div></div>
                        <div class="health-item"><div class="label">Uptime</div><div class="value">${formatUptime(health.uptime_seconds)}</div></div>
                        <div class="health-item"><div class="label">Last Collection</div><div class="value">${timeAgo(health.last_collection) || '\u2014'}</div></div>
                        <div class="health-item"><div class="label">Analysis Mode</div><div class="value">${
                            health.llm_status === 'active' ? `${statusDot('ok')} AI` :
                            health.llm_status === 'configured' ? `${statusDot('idle')} AI (not responding)` :
                            `${statusDot('idle')} Standard`
                        }</div></div>
                        ${health.llm_status === 'configured' ? `<div class="health-item" style="grid-column:1/-1;"><div class="value text-xs" style="font-family:var(--font-sans);color:var(--warning);">AI provider is configured but not responding. Check your API key and credits.</div></div>` : ''}
                        ${health.llm_status === 'disabled' ? `<div class="health-item" style="grid-column:1/-1;"><div class="value text-xs text-muted" style="font-family:var(--font-sans);">All core features are active. Add an AI provider to unlock smart analysis, insights, and natural language queries.</div></div>` : ''}
                        <div class="health-item"><div class="label">Version</div><div class="value text-mono">${esc(health.version || '\u2014')}</div></div>
                    </div>
                </div>`;
            }

            const agentList = ensureArray(agents, 'items', 'agents');
            if (agentList.length) {
                agentEl.innerHTML = `<div class="table-wrap"><table>
                    <thead><tr><th>Agent</th><th>Status</th><th>Last Run</th><th class="num">Duration</th><th class="num">Runs</th><th class="num">Errors</th></tr></thead>
                    <tbody>${agentList.map(a => `<tr>
                        <td class="font-medium">${esc(a.name || a.agent_id)}</td>
                        <td>${statusDot(a.status)} ${esc(a.status)}</td>
                        <td class="text-sm text-muted">${timeAgo(a.last_run)}</td>
                        <td class="num">${a.last_duration_ms != null ? (a.last_duration_ms / 1000).toFixed(1) + 's' : '\u2014'}</td>
                        <td class="num">${a.run_count ?? '\u2014'}</td>
                        <td class="num ${(a.error_count || 0) > 0 ? 'text-danger' : ''}">${a.error_count ?? 0}</td>
                    </tr>`).join('')}</tbody></table></div>`;
            } else {
                agentEl.innerHTML = renderEmpty(null, 'No agent data available.');
            }

            const srcList = ensureArray(sources, 'items', 'sources');
            if (srcList.length) {
                sourceEl.innerHTML = `<div class="table-wrap"><table>
                    <thead><tr><th>Name</th><th>Type</th><th>Domain</th><th>Status</th><th>Enabled</th><th>Last Fetched</th></tr></thead>
                    <tbody>${srcList.map(s => `<tr>
                        <td class="font-medium">${esc(s.name)}</td>
                        <td><span class="badge badge-muted">${esc(s.source_type)}</span></td>
                        <td class="text-sm text-mono">${esc(s.domain || '\u2014')}</td>
                        <td>${statusDot(s.last_status || 'idle')} ${esc(s.last_status || 'idle')}</td>
                        <td>${s.enabled ? '<span class="text-success">Yes</span>' : '<span class="text-muted">No</span>'}</td>
                        <td class="text-sm text-muted">${timeAgo(s.last_fetched_at)}</td>
                    </tr>`).join('')}</tbody></table></div>`;
            } else {
                sourceEl.innerHTML = renderEmpty(null, 'No sources configured.');
            }
        } catch (e) {
            healthEl.innerHTML = renderError('health: ' + e.message);
        }
    }

    // ---------------------------------------------------------------
    // Telegram status (reads from /health endpoint)
    // ---------------------------------------------------------------
    function updateTelegramStatus(health) {
        const dot = document.getElementById('telegram-dot');
        const label = document.getElementById('telegram-label');
        const help = document.getElementById('telegram-help');
        if (!dot || !label || !health) return;

        const enabled = !!health.telegram_enabled;
        const configured = !!health.telegram_configured;

        if (enabled && configured) {
            dot.className = 'status-dot status-ok';
            label.textContent = 'Active';
            if (help) help.style.display = 'none';
        } else if (enabled && !configured) {
            dot.className = 'status-dot status-degraded';
            label.textContent = 'Enabled but not configured';
            if (help) {
                help.style.display = '';
                help.innerHTML = 'Bot token detected but chat ID is missing. Add your Telegram chat ID to <code>~/.axion.env</code> and restart Axion.';
            }
        } else {
            dot.className = 'status-dot status-stopped';
            label.textContent = 'Disabled';
            // Keep the original HTML setup instructions visible (don't overwrite)
            if (help) help.style.display = '';
        }
    }

    // (Sidebar removed — dead code cleaned up)

    // ================================================================
    // Legacy Actions (Upload, Acknowledge, etc.)
    // ================================================================
    window.uploadPortfolio = function () {
        const modal = $('#upload-modal');
        $('#upload-form').reset();
        $('#file-info').textContent = '';
        $('#upload-btn').disabled = true;
        modal.showModal();
    };

    window.onFileSelected = function (input) {
        const file = input.files[0];
        const info = $('#file-info');
        const btn = $('#upload-btn');
        const aiNotice = $('#upload-ai-notice');
        if (file) {
            info.textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
            const ext = file.name.split('.').pop().toLowerCase();
            const isImage = ['png', 'jpg', 'jpeg'].includes(ext);
            const isPdf = ext === 'pdf';
            if (aiNotice) {
                if (isImage) {
                    aiNotice.style.display = '';
                    aiNotice.style.background = 'var(--warning-bg)';
                    aiNotice.style.color = 'var(--warning)';
                    aiNotice.textContent = 'Image extraction requires an AI provider. Configure one in Settings if you haven\u2019t already.';
                } else if (isPdf) {
                    aiNotice.style.display = '';
                    aiNotice.style.background = 'var(--surface)';
                    aiNotice.style.color = 'var(--muted)';
                    aiNotice.textContent = 'Structured PDFs with tables are parsed directly. Scanned PDFs will use AI vision if available.';
                } else {
                    aiNotice.style.display = 'none';
                }
            }
            btn.disabled = false;
        } else {
            info.textContent = '';
            btn.disabled = true;
            if (aiNotice) aiNotice.style.display = 'none';
        }
    };

    window.submitUpload = async function () {
        const fileInput = $('#portfolio-file');
        const file = fileInput.files[0];
        if (!file) return;

        await withLoading($('#upload-btn'), 'Extracting...', async () => {
            try {
                const fd = new FormData();
                fd.append('file', file);
                const res = await fetch(API.extract, { method: 'POST', body: fd });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || 'Server error: ' + res.status);
                }
                const data = await res.json();

                $('#upload-modal').close();

                if (data.status === 'ai_required') {
                    showToast(data.message, 'warning');
                    return;
                }
                if (data.status === 'empty' || !data.rows || data.rows.length === 0) {
                    showToast(data.message || 'No data found in file.', 'warning');
                    return;
                }

                // Open review modal with extracted data
                openReviewModal(data);
            } catch (e) {
                showToast('Could not extract portfolio data. Try a different file format. ' + e.message, 'error');
            }
        });
    };

    // Store extracted rows for the review modal
    let _reviewRows = [];

    window.openReviewModal = function openReviewModal(data) {
        _reviewRows = data.rows;
        const tbody = $('#review-tbody');
        const msg = $('#review-message');
        const summary = $('#review-summary');
        const title = $('#review-modal-title');

        title.textContent = 'Review: ' + esc(data.filename);
        // Show extraction method if available
        const method = data.extraction_method || data.method || '';
        const methodLabel = method ? ` (via ${method})` : '';
        msg.textContent = (data.message || '') + methodLabel;

        tbody.innerHTML = '';
        data.rows.forEach((row, i) => {
            const tr = document.createElement('tr');
            tr.dataset.index = i;
            tr.innerHTML = `
                <td><input type="checkbox" class="review-check" data-index="${i}" checked></td>
                <td><input type="text" class="review-field" data-field="ticker" value="${esc(row.ticker || '')}"></td>
                <td><input type="text" class="review-field" data-field="name" value="${esc(row.name || '')}"></td>
                <td><input type="number" class="review-field" data-field="quantity" step="any" value="${row.quantity ?? ''}"></td>
                <td><input type="number" class="review-field" data-field="current_price" step="any" value="${row.current_price ?? ''}"></td>
                <td><input type="number" class="review-field" data-field="avg_cost_basis" step="any" value="${row.avg_cost_basis ?? ''}"></td>
                <td><input type="number" class="review-field" data-field="market_value" step="any" value="${row.market_value ?? ''}"></td>
                <td><input type="text" class="review-field" data-field="currency" value="${esc(row.currency || 'USD')}" style="width:55px;"></td>
            `;
            tbody.appendChild(tr);
        });

        // Toggle row styling when checkbox changes
        tbody.querySelectorAll('.review-check').forEach(cb => {
            cb.addEventListener('change', () => {
                const tr = cb.closest('tr');
                tr.classList.toggle('row-excluded', !cb.checked);
                updateReviewSummary();
            });
        });

        updateReviewSummary();
        $('#review-modal').showModal();
    }

    function updateReviewSummary() {
        const checks = $$('#review-tbody .review-check');
        const selected = Array.from(checks).filter(c => c.checked).length;
        const total = checks.length;
        const summary = $('#review-summary');
        if (summary) summary.textContent = `${selected} of ${total} rows selected for import`;
    }

    window.selectAllReviewRows = function () {
        const checks = $$('#review-tbody .review-check');
        const allChecked = Array.from(checks).every(c => c.checked);
        checks.forEach(c => {
            c.checked = !allChecked;
            c.closest('tr').classList.toggle('row-excluded', allChecked);
        });
        updateReviewSummary();
    };

    // --- Review-modal field validation (mirrors server-side rules) ---
    function validateReviewRows() {
        let errors = 0;
        // Clear all previous highlights
        $$('#review-tbody .field-invalid').forEach(el => el.classList.remove('field-invalid'));

        $$('#review-tbody tr').forEach(tr => {
            const cb = tr.querySelector('.review-check');
            if (!cb || !cb.checked) return; // skip unchecked rows

            const field = (name) => tr.querySelector(`.review-field[data-field="${name}"]`);
            const markBad = (input) => { if (input) { input.classList.add('field-invalid'); errors++; } };

            // Ticker: required, 1-10 chars, alphanumeric + dot
            const tickerInput = field('ticker');
            const ticker = (tickerInput.value || '').trim().toUpperCase();
            if (!ticker || ticker.length > 10 || !/^[A-Z0-9.]+$/.test(ticker)) {
                markBad(tickerInput);
            }

            // Quantity: required, > 0
            const qtyInput = field('quantity');
            const qty = parseFloat(qtyInput.value);
            if (isNaN(qty) || qty <= 0) {
                markBad(qtyInput);
            }

            // Price: optional, but if present must be >= 0
            const priceInput = field('current_price');
            if (priceInput && priceInput.value !== '') {
                const p = parseFloat(priceInput.value);
                if (isNaN(p) || p < 0) markBad(priceInput);
            }

            // Cost basis: optional, >= 0
            const costInput = field('avg_cost_basis');
            if (costInput && costInput.value !== '') {
                const c = parseFloat(costInput.value);
                if (isNaN(c) || c < 0) markBad(costInput);
            }

            // Market value: optional, >= 0
            const mvInput = field('market_value');
            if (mvInput && mvInput.value !== '') {
                const mv = parseFloat(mvInput.value);
                if (isNaN(mv) || mv < 0) markBad(mvInput);
            }

            // Currency: trim, uppercase, default USD, must be 3 uppercase letters
            const curInput = field('currency');
            if (curInput) {
                let cur = (curInput.value || '').trim().toUpperCase();
                if (!cur) cur = 'USD';
                curInput.value = cur; // normalize in-place so user sees it
                if (!/^[A-Z]{3}$/.test(cur)) {
                    markBad(curInput);
                }
            }
        });

        return errors;
    }

    // Live revalidation: clear error highlight on edit
    (function () {
        const tbody = $('#review-tbody');
        if (tbody) {
            tbody.addEventListener('input', function (e) {
                if (e.target.classList.contains('field-invalid')) {
                    e.target.classList.remove('field-invalid');
                }
            });
        }
    })();

    window.confirmReviewedImport = async function () {
        // --- Frontend validation gate ---
        const errorCount = validateReviewRows();
        if (errorCount > 0) {
            showToast(errorCount + ' field(s) have validation errors \u2014 fix the highlighted fields.', 'warning');
            return;
        }

        const btn = $('#review-confirm-btn');
        const rows = [];

        $$('#review-tbody tr').forEach(tr => {
            const cb = tr.querySelector('.review-check');
            if (!cb || !cb.checked) return;

            const get = (field) => {
                const input = tr.querySelector(`.review-field[data-field="${field}"]`);
                return input ? input.value : null;
            };
            const getNum = (field) => {
                const v = get(field);
                return v !== null && v !== '' ? parseFloat(v) : null;
            };

            const ticker = (get('ticker') || '').trim().toUpperCase();
            if (!ticker) return;

            rows.push({
                ticker: ticker,
                name: get('name') || null,
                quantity: getNum('quantity'),
                current_price: getNum('current_price'),
                avg_cost_basis: getNum('avg_cost_basis'),
                market_value: getNum('market_value'),
                currency: (get('currency') || 'USD').trim().toUpperCase(),
            });
        });

        if (rows.length === 0) {
            showToast('No rows selected for import.', 'warning');
            return;
        }

        await withLoading(btn, 'Importing...', async () => {
            try {
                const res = await fetch(API.importReviewed, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ rows: rows, portfolio_id: getActivePortfolioId() }),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    // Surface server validation errors clearly
                    const detail = err.detail;
                    if (typeof detail === 'string') {
                        // Duplicate-ticker or batch-level error — readable message
                        throw new Error(detail);
                    } else if (Array.isArray(detail)) {
                        // Pydantic per-field 422 errors — summarize usefully
                        const msgs = detail.map(e => {
                            const loc = (e.loc || []).slice(1).join(' \u2192 ');
                            return loc ? loc + ': ' + e.msg : e.msg;
                        });
                        throw new Error('Validation errors:\n' + msgs.join('\n'));
                    }
                    throw new Error('Import failed (HTTP ' + res.status + ')');
                }
                const data = await res.json();
                $('#review-modal').close();

                const imported = data.holdings_imported ?? 0;
                const updated = data.holdings_updated ?? 0;
                const errors = data.errors || [];
                showToast(`Import complete: ${imported} new, ${updated} updated, ${errors.length} errors.`);
                refreshTab('holdings');
                refreshTab('exposures');
            } catch (e) {
                showToast('Import failed: ' + e.message, 'error');
            }
        });
    };

    window.acknowledgeAlert = async function (id) {
        try {
            const res = await fetch(API.alertAck(id), { method: 'POST' });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            showToast('Alert acknowledged');
            refreshTab('alerts');
            // (sidebar removed)
        } catch (e) {
            showToast('Could not acknowledge alert: ' + e.message, 'error');
        }
    };

    window.runAction = async function (agentId) {
        try {
            const res = await fetch(API.agentRun(agentId), { method: 'POST' });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            await res.json();
            showToast(`${agentId} agent triggered`);
        } catch (e) {
            showToast(`Failed to run ${agentId}: ${e.message}`, 'error');
        }
    };

    window.exportCSV = function (type, format) {
        const fmt = format || 'csv';
        const ext = fmt === 'xlsx' ? 'xlsx' : fmt === 'pdf' ? 'pdf' : 'csv';
        const url = `/api/v1/export/${type}?format=${fmt}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = `${type}_export.${ext}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        showToast(`Downloading ${type} ${fmt.toUpperCase()}...`);
    };

    window.triggerCollection = async function () {
        try {
            const res = await fetch('/api/v1/agents/collection/run', { method: 'POST' });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            showToast('Collection started — events will appear shortly');
        } catch (e) {
            showToast('Collection failed: ' + e.message, 'error');
        }
    };

    window.generateDigest = async function () {
        const digestEl = $('#digest-content');
        if (digestEl && digestEl.closest('.tab-panel')?.classList.contains('active')) {
            digestEl.innerHTML = '<div class="spinner">Generating digest...</div>';
        }
        try {
            const res = await fetch(API.digestGen, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ digest_type: 'ad-hoc', scope: 'portfolio' })
            });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            showToast('Digest generation queued');
            setTimeout(() => refreshTab('digest'), 4000);
        } catch (e) {
            showToast('Digest generation failed: ' + e.message, 'error');
            if (digestEl) refreshTab('digest');
        }
    };

    // ================================================================
    // Reset Portfolio
    // ================================================================
    window.openResetPortfolio = function () {
        const modal = $('#reset-modal');
        $('#reset-confirm-input').value = '';
        modal.showModal();
        $('#reset-confirm-input').focus();
    };

    window.confirmResetPortfolio = async function () {
        const input = $('#reset-confirm-input');
        if (input.value.trim() !== 'RESET') {
            showToast('Type RESET to confirm', 'error');
            input.focus();
            return;
        }

        await withLoading($('#reset-btn'), 'Resetting...', async () => {
            try {
                const res = await fetch('/api/v1/portfolio/reset', { method: 'POST' });
                if (!res.ok) throw new Error('HTTP ' + res.status);
                $('#reset-modal').close();
                showToast('All data cleared. Start fresh!');

                // Refresh everything
                Object.keys(tabLoaded).forEach(k => tabLoaded[k] = false);
                switchTab('portfolio');
                // (sidebar removed)
            } catch (e) {
                showToast('Reset failed: ' + e.message, 'error');
            }
        });
    };

    // ================================================================
    // ================================================================
    // Command Center
    // ================================================================
    const _cmdMessages = [];  // Session-only transcript

    function loadCommand() {
        // Update mode indicator
        _updateCmdMode();
        // Wire up enter key
        const input = document.getElementById('cmd-input');
        if (input && !input._cmdWired) {
            input._cmdWired = true;
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendCommand(); }
            });
        }
        // Wire up example prompt chips
        document.querySelectorAll('.cmd-prompt-chip').forEach(chip => {
            if (!chip._cmdWired) {
                chip._cmdWired = true;
                chip.addEventListener('click', () => {
                    const prompt = chip.dataset.prompt;
                    if (prompt) {
                        document.getElementById('cmd-input').value = prompt;
                        sendCommand();
                    }
                });
            }
        });
    }

    async function _updateCmdMode() {
        try {
            const h = await fetchJSON(API.health).catch(() => null);
            const badgeEl = document.getElementById('cmd-mode-badge');
            if (h) {
                const mode = h.llm_available ? 'AI' : 'Standard';
                const tip = h.llm_available ? '' : ' — portfolio queries use core lookups. Add an AI key in Settings for natural language analysis.';
                if (badgeEl) {
                    badgeEl.style.display = '';
                    badgeEl.title = `${mode}${tip}`;
                    const badgeDot = badgeEl.querySelector('.dot');
                    const badgeText = badgeEl.querySelector('.cmd-mode-label-text');
                    if (badgeDot) badgeDot.style.background = h.llm_available ? 'var(--success)' : 'var(--warning)';
                    if (badgeText) badgeText.textContent = mode;
                }
            }
        } catch {}
    }

    window.sendCommand = async function () {
        const input = document.getElementById('cmd-input');
        const query = (input?.value || '').trim();
        if (!query) return;

        const transcript = document.getElementById('cmd-transcript');
        const welcome = document.getElementById('cmd-welcome');
        const clearBtn = document.getElementById('cmd-clear');
        const sendBtn = document.getElementById('cmd-send');

        // Hide welcome, show clear
        if (welcome) welcome.style.display = 'none';
        if (clearBtn) clearBtn.style.display = '';

        // Show user message
        const userDiv = document.createElement('div');
        userDiv.className = 'cmd-msg';
        userDiv.innerHTML = `<div class="cmd-msg-label cmd-msg-label-right">You</div><div class="cmd-msg-user"><div class="cmd-msg-user-bubble">${esc(query)}</div></div>`;
        transcript.appendChild(userDiv);

        // Clear input and disable
        input.value = '';
        input.disabled = true;
        if (sendBtn) sendBtn.disabled = true;

        // Show loading
        const loadDiv = document.createElement('div');
        loadDiv.className = 'cmd-loading';
        loadDiv.innerHTML = '<div class="spinner" style="width:16px;height:16px;border-width:2px;"></div> Thinking...';
        transcript.appendChild(loadDiv);
        transcript.scrollTop = transcript.scrollHeight;

        try {
            // Phase 9E: include the active portfolio so the backend
            // can scope holdings, alerts, events, and factor /
            // relationship touchpoints to exactly this portfolio.
            const res = await fetch('/api/v1/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    query,
                    portfolio_id: getActivePortfolioId(),
                }),
            });

            loadDiv.remove();

            if (!res.ok) {
                _appendCmdError('Server error: HTTP ' + res.status);
                return;
            }

            const data = await res.json();
            _appendCmdResponse(data);
            _cmdMessages.push({ query, response: data });

        } catch (e) {
            loadDiv.remove();
            _appendCmdError('Could not reach Axion: ' + e.message);
        } finally {
            input.disabled = false;
            if (sendBtn) sendBtn.disabled = false;
            input.focus();
        }
    };

    function _appendCmdResponse(data) {
        const transcript = document.getElementById('cmd-transcript');
        const div = document.createElement('div');
        div.className = 'cmd-msg';

        // Determine card modifier
        let cardClass = 'cmd-msg-response';
        if (data.actions_taken?.length) cardClass += ' cmd-action';
        else if (data.warnings?.length) cardClass += ' cmd-warning';

        // Format answer — basic markdown: **bold**, bullet points
        let answer = esc(data.answer || 'No response.');
        answer = answer.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        answer = answer.replace(/^  - /gm, '&nbsp;&nbsp;• ');
        answer = answer.replace(/^- /gm, '• ');

        // Meta chips
        const metaParts = [];
        if (data.mode) {
            const dot = data.mode === 'ai-enhanced' ? 'green' : 'yellow';
            const modeLabel = data.mode === 'rule-based' ? 'Standard' : data.mode;
            metaParts.push(`<span class="cmd-meta-chip"><span class="dot ${dot}"></span>${esc(modeLabel)}</span>`);
        }
        if (data.provider) {
            metaParts.push(`<span class="cmd-meta-chip">${esc(data.provider)}</span>`);
        }
        if (data.context_summary) {
            metaParts.push(`<span class="cmd-meta-chip">${esc(data.context_summary)}</span>`);
        }
        if (data.actions_taken?.length) {
            for (const a of data.actions_taken) {
                metaParts.push(`<span class="cmd-meta-chip" style="color:var(--success);">&#10003; ${esc(a.replace(/_/g, ' '))}</span>`);
            }
        }

        // Warnings
        let warningHtml = '';
        if (data.warnings?.length) {
            warningHtml = data.warnings.map(w =>
                `<div class="text-xs" style="color:var(--warning);margin-top:0.35rem;">${esc(w)}</div>`
            ).join('');
        }

        div.innerHTML = `<div class="cmd-msg-label">Axion</div><div class="${cardClass}">
            <div class="cmd-msg-answer">${answer}</div>
            ${warningHtml}
            ${metaParts.length ? `<div class="cmd-msg-meta">${metaParts.join('')}</div>` : ''}
        </div>`;

        transcript.appendChild(div);
        transcript.scrollTop = transcript.scrollHeight;
    }

    function _appendCmdError(msg) {
        const transcript = document.getElementById('cmd-transcript');
        const div = document.createElement('div');
        div.className = 'cmd-msg';
        div.innerHTML = `<div class="cmd-msg-response cmd-warning">
            <div class="cmd-msg-answer">${esc(msg)}</div>
            <div class="text-xs text-muted" style="margin-top:0.35rem;">Try again or check Health tab for system status.</div>
        </div>`;
        transcript.appendChild(div);
        transcript.scrollTop = transcript.scrollHeight;
    }

    window.clearCommand = function () {
        const transcript = document.getElementById('cmd-transcript');
        const welcome = document.getElementById('cmd-welcome');
        const clearBtn = document.getElementById('cmd-clear');
        transcript.innerHTML = '';
        if (welcome) { transcript.appendChild(welcome); welcome.style.display = ''; }
        if (clearBtn) clearBtn.style.display = 'none';
        _cmdMessages.length = 0;
    };

    // ================================================================
    // Settings (localStorage-based)
    // ================================================================
    const SETTINGS_KEY = 'axion_settings';
    const DEFAULT_SETTINGS = {
        refreshInterval: 60000,
        desktopNotif: false,
        defaultTab: 'portfolio',
    };

    function getSettings() {
        try {
            const raw = localStorage.getItem(SETTINGS_KEY);
            return raw ? { ...DEFAULT_SETTINGS, ...JSON.parse(raw) } : { ...DEFAULT_SETTINGS };
        } catch { return { ...DEFAULT_SETTINGS }; }
    }

    let _autoRefreshTimer = null;

    function applySettings(s) {
        // Auto-refresh: periodically reload the active tab's data
        if (_autoRefreshTimer) { clearInterval(_autoRefreshTimer); _autoRefreshTimer = null; }
        const interval = parseInt(s.refreshInterval) || 0;
        if (interval > 0) {
            _autoRefreshTimer = setInterval(() => {
                const activeTab = document.querySelector('.tab-link.active');
                if (!activeTab) return;
                const tabName = activeTab.dataset.tab;
                // Refresh the active primary tab (and active sub-tab if applicable)
                if (tabName === 'portfolio') {
                    const activeSub = document.querySelector('#tab-portfolio .sub-tab.active');
                    if (activeSub) refreshTab(activeSub.dataset.subtab);
                } else if (tabName === 'intelligence') {
                    const activeSub = document.querySelector('#tab-intelligence .sub-tab.active');
                    if (activeSub) refreshTab(activeSub.dataset.subtab);
                } else if (tabLoaders[tabName]) {
                    refreshTab(tabName);
                }
            }, interval);
        }
    }

    function loadSettings() {
        const s = getSettings();
        const el = (id) => document.getElementById(id);
        if (el('setting-refresh')) el('setting-refresh').value = String(s.refreshInterval);
        if (el('setting-desktop-notif')) el('setting-desktop-notif').checked = s.desktopNotif;
        if (el('setting-default-tab')) el('setting-default-tab').value = s.defaultTab;
    }

    window.saveSettings = function () {
        const s = {
            refreshInterval: parseInt(document.getElementById('setting-refresh')?.value) || 60000,
            desktopNotif: document.getElementById('setting-desktop-notif')?.checked || false,
            defaultTab: document.getElementById('setting-default-tab')?.value || 'portfolio',
        };
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
        applySettings(s);
        showToast('Settings saved');

        if (s.desktopNotif && Notification.permission === 'default') {
            Notification.requestPermission();
        }
    };

    window.resetSettings = function () {
        localStorage.removeItem(SETTINGS_KEY);
        loadSettings();
        applySettings(DEFAULT_SETTINGS);
        showToast('Settings reset to defaults');
    };

    // ================================================================
    // Quit Axion
    // ================================================================

    window.quitAxion = async function () {
        if (!confirm('Quit Axion?\n\nThis will stop the intelligence engine and close the application.')) return;
        showToast('Shutting down Axion...');
        try {
            await fetch('/api/v1/shutdown', { method: 'POST' });
        } catch (e) {
            // Server may close before response — that's expected
        }
        // Also write quit signal file for the desktop shell
        try {
            await fetch('/api/v1/settings/quit-signal', { method: 'POST' });
        } catch (e) { /* ignore */ }
    };

    // ================================================================
    // API Key Configuration
    // ================================================================

    // ================================================================
    // AI Provider Selection — Primary + Fallback model
    // ================================================================
    const _KEY_PLACEHOLDERS = { anthropic: 'sk-ant-...', openai: 'sk-...', google: 'AIza...' };

    function _showRestartBanner() {
        const banner = document.getElementById('ai-restart-banner');
        if (banner) banner.style.display = '';
    }

    function updateProviderUI() {
        const primarySel = document.getElementById('setting-primary-provider');
        const backupSel = document.getElementById('setting-backup-provider');
        const primaryKeyCard = document.getElementById('provider-primary-key-card');
        const backupKeyCard = document.getElementById('provider-backup-key-card');
        const fallbackSection = document.getElementById('fallback-section');
        const primaryKeyInput = document.getElementById('setting-primary-key');
        const backupKeyInput = document.getElementById('setting-backup-key');
        if (!primarySel) return;

        const primary = primarySel.value;

        // Show/hide primary key card
        if (primaryKeyCard) primaryKeyCard.style.display = primary ? '' : 'none';

        // Show/hide fallback section (only when primary is set)
        if (fallbackSection) fallbackSection.style.display = primary ? '' : 'none';

        // Filter fallback options: exclude current primary
        if (backupSel) {
            const currentBackup = backupSel.value;
            const allOptions = [
                { value: '', label: 'None' },
                { value: 'anthropic', label: 'Anthropic (Claude)' },
                { value: 'openai', label: 'OpenAI (GPT)' },
                { value: 'google', label: 'Google (Gemini)' },
            ];
            backupSel.innerHTML = '';
            allOptions.forEach(opt => {
                if (opt.value && opt.value === primary) return; // exclude primary
                const el = document.createElement('option');
                el.value = opt.value;
                el.textContent = opt.label;
                backupSel.appendChild(el);
            });
            // Restore previous selection if still valid
            if ([...backupSel.options].some(o => o.value === currentBackup)) {
                backupSel.value = currentBackup;
            } else {
                backupSel.value = '';
            }
        }

        const backup = backupSel ? backupSel.value : '';

        // Show/hide backup key card
        if (backupKeyCard) backupKeyCard.style.display = backup ? '' : 'none';

        // Update key placeholders
        if (primaryKeyInput) primaryKeyInput.placeholder = _KEY_PLACEHOLDERS[primary] || 'API key...';
        if (backupKeyInput) backupKeyInput.placeholder = _KEY_PLACEHOLDERS[backup] || 'API key...';
    }

    async function loadApiKeyStatus() {
        try {
            const res = await fetch('/api/v1/settings/api-key');
            if (!res.ok) return;
            const data = await res.json();
            const dot = document.getElementById('api-key-dot');
            const mode = document.getElementById('api-key-mode');
            const hint = document.getElementById('api-key-hint');
            const actions = document.getElementById('api-key-actions');
            const primaryStatus = document.getElementById('primary-key-status');
            const backupStatus = document.getElementById('backup-key-status');
            const primarySel = document.getElementById('setting-primary-provider');
            const backupSel = document.getElementById('setting-backup-provider');
            if (!dot || !mode) return;

            // Set dropdowns to reflect server state
            if (primarySel) primarySel.value = data.primary_provider || '';
            updateProviderUI(); // rebuild fallback options before setting backup
            if (backupSel && data.backup_provider) backupSel.value = data.backup_provider;
            updateProviderUI(); // refresh visibility

            // Status display
            const hasPrimary = !!data.primary_provider;
            const hasKey = data.configured;

            if (hasPrimary && hasKey && data.llm_available) {
                dot.className = 'status-dot status-connected';
                const label = data.primary_provider + (data.backup_provider ? ' + ' + data.backup_provider + ' fallback' : '');
                mode.textContent = label + ' — active';
                hint.textContent = 'AI provider ready. Classification, analysis, and digest generation use AI for richer results.';
                if (actions) actions.style.display = '';
            } else if (hasPrimary && hasKey && !data.llm_available) {
                dot.className = 'status-dot status-degraded';
                mode.textContent = data.primary_provider + ' — configured (restart required)';
                hint.textContent = 'API key saved. Restart Axion to activate AI features.';
                if (actions) actions.style.display = '';
            } else if (hasPrimary && !hasKey) {
                dot.className = 'status-dot status-stopped';
                mode.textContent = data.primary_provider + ' selected — no key';
                hint.textContent = 'Add your ' + data.primary_provider + ' API key below to enable AI features.';
                if (actions) actions.style.display = 'none';
            } else {
                dot.className = 'status-dot status-stopped';
                mode.textContent = 'AI disabled';
                hint.textContent = 'Select a provider above and add an API key to enable AI analysis.';
                if (actions) actions.style.display = 'none';
            }

            // Per-provider key status
            if (primaryStatus) {
                const prov = (data.providers || []).find(p => p.provider === data.primary_provider);
                if (prov && prov.configured) {
                    primaryStatus.innerHTML = '<span class="text-xs text-success">Key: ' + (prov.masked_key || 'Configured') + '</span>';
                } else if (data.primary_provider) {
                    primaryStatus.innerHTML = '<span class="text-xs text-muted">No key configured</span>';
                } else {
                    primaryStatus.innerHTML = '';
                }
            }
            if (backupStatus) {
                if (data.backup_provider) {
                    const prov = (data.providers || []).find(p => p.provider === data.backup_provider);
                    if (prov && prov.configured) {
                        backupStatus.innerHTML = '<span class="text-xs text-success">Key: ' + (prov.masked_key || 'Configured') + '</span>';
                    } else {
                        backupStatus.innerHTML = '<span class="text-xs text-muted">No key configured for ' + data.backup_provider + '</span>';
                    }
                } else {
                    backupStatus.innerHTML = '';
                }
            }
            // Update capabilities reference dot color
            const aiFeaturesRow = document.getElementById('ai-features-row');
            if (aiFeaturesRow) {
                const aiDot = aiFeaturesRow.querySelector('.dot');
                const aiLabel = aiFeaturesRow.querySelector('.capabilities-ref-label .text-xs');
                if (data.llm_available) {
                    if (aiDot) aiDot.className = 'dot green';
                    if (aiLabel) aiLabel.textContent = '(active)';
                } else {
                    if (aiDot) aiDot.className = 'dot yellow';
                    if (aiLabel) aiLabel.textContent = '(requires provider key)';
                }
            }

            // Hide restart banner if LLM is already active and key is configured
            if (data.llm_available && data.configured) {
                const banner = document.getElementById('ai-restart-banner');
                if (banner) banner.style.display = 'none';
            }
        } catch (e) {
            console.warn('Failed to load API key status:', e);
        }
    }

    // Save provider selection (primary + fallback)
    window.saveProviderSelection = async function () {
        const primarySel = document.getElementById('setting-primary-provider');
        const backupSel = document.getElementById('setting-backup-provider');
        const statusEl = document.getElementById('provider-save-status');
        const primary = primarySel ? primarySel.value : '';
        const fallback = backupSel ? backupSel.value : '';

        if (fallback && fallback === primary) {
            showToast('Fallback cannot be the same as primary.', 'warning');
            return;
        }

        try {
            const res = await fetch('/api/v1/settings/provider', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ primary, fallback }),
            });
            const data = await res.json();
            if (!res.ok) { showToast(data.detail || 'Save failed.', 'error'); return; }
            showToast(data.message, 'success');
            _showRestartBanner();
            if (statusEl) {
                statusEl.textContent = 'Saved. Restart to apply.';
                setTimeout(() => { statusEl.textContent = ''; }, 5000);
            }
        } catch (e) {
            showToast('Failed to save provider: ' + e.message, 'error');
        }
    };

    window.removeApiKey = async function () {
        if (!confirm('Remove all API keys and disable AI?')) return;
        try {
            const res = await fetch('/api/v1/settings/api-key', { method: 'DELETE' });
            const data = await res.json();
            if (!res.ok) { showToast(data.detail || 'Remove failed.', 'error'); return; }
            showToast(data.message, 'success');
            await loadApiKeyStatus();
        } catch (e) {
            showToast('Failed to remove key: ' + e.message, 'error');
        }
    };

    // ================================================================
    // Sources Management
    // ================================================================
    let allSources = [];

    const SOURCE_PRESETS = [
        { name: 'Federal Reserve Press Releases', url: 'https://www.federalreserve.gov/feeds/press_all.xml', domain: 'www.federalreserve.gov', source_type: 'rss', parser_id: 'rss_generic', priority: 1, trust_level: 'premium' },
        { name: 'ECB Press Releases', url: 'https://www.ecb.europa.eu/rss/press.html', domain: 'www.ecb.europa.eu', source_type: 'rss', parser_id: 'rss_generic', priority: 1, trust_level: 'premium' },
        { name: 'Google News Business', url: 'https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB', domain: 'news.google.com', source_type: 'rss', parser_id: 'rss_generic', priority: 3, trust_level: 'standard' },
        { name: 'WSJ Markets', url: 'https://feeds.a.dj.com/rss/RSSMarketsMain.xml', domain: 'feeds.a.dj.com', source_type: 'rss', parser_id: 'rss_generic', priority: 2, trust_level: 'premium' },
        { name: 'MarketWatch Top Stories', url: 'https://feeds.marketwatch.com/marketwatch/topstories', domain: 'feeds.marketwatch.com', source_type: 'rss', parser_id: 'rss_generic', priority: 3, trust_level: 'standard' },
        { name: 'Seeking Alpha Market News', url: 'https://seekingalpha.com/market_currents.xml', domain: 'seekingalpha.com', source_type: 'rss', parser_id: 'rss_generic', priority: 3, trust_level: 'standard' },
        { name: 'Investing.com News', url: 'https://www.investing.com/rss/news.rss', domain: 'www.investing.com', source_type: 'rss', parser_id: 'rss_generic', priority: 4, trust_level: 'standard' },
    ];

    async function loadSources() {
        try {
            const data = await fetchJSON(API.sources);
            allSources = data;
            renderSourcesTable(data);
        } catch (e) {
            $('#sources-table').innerHTML = `<div class="empty-state"><p>Failed to load sources.</p></div>`;
        }
    }

    function renderSourcesTable(sources) {
        const q = ($('#sources-search') || {}).value || '';
        const filtered = q ? sources.filter(s =>
            s.name.toLowerCase().includes(q.toLowerCase()) ||
            (s.url || '').toLowerCase().includes(q.toLowerCase()) ||
            s.domain.toLowerCase().includes(q.toLowerCase())
        ) : sources;

        if (!filtered.length) {
            $('#sources-table').innerHTML = `
                <div class="empty-state">
                    <h3>No sources configured</h3>
                    <p class="text-sm text-muted">Add feeds to collect financial news.</p>
                    <div style="margin-top:1rem;display:flex;gap:0.5rem;justify-content:center;">
                        <button class="btn btn-primary" onclick="openAddSource()">+ Add Source</button>
                        <button class="btn btn-outline" onclick="openQuickAddSources()">Quick Add</button>
                    </div>
                </div>`;
            return;
        }

        const typeBadge = (t) => {
            const colors = { rss: 'var(--accent)', api: '#a78bfa', scrape: '#f59e0b' };
            return `<span class="badge" style="background:${colors[t] || 'var(--muted-fg)'};color:#fff;font-size:0.65rem;">${esc(t.toUpperCase())}</span>`;
        };
        const statusDot = (s) => {
            const c = s === 'ok' ? 'var(--success)' : s === 'error' ? 'var(--danger)' : 'var(--muted-fg)';
            return `<span class="dot" style="background:${c};"></span> ${esc(s || 'idle')}`;
        };
        const truncUrl = (u) => {
            if (!u) return '<span class="text-muted">—</span>';
            try { return esc(new URL(u).hostname + new URL(u).pathname.substring(0, 30)); } catch { return esc(u.substring(0, 40)); }
        };

        const rows = filtered.map(s => `
            <tr>
                <td><a href="${esc(s.url || '#')}" target="_blank" rel="noopener" class="ticker-link">${esc(s.name)}</a></td>
                <td class="text-muted text-xs" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(s.url || '')}">${truncUrl(s.url)}</td>
                <td>${typeBadge(s.source_type)}</td>
                <td>
                    <label class="toggle-label" style="margin:0;gap:0.25rem;">
                        <input type="checkbox" class="toggle-input" ${s.enabled ? 'checked' : ''} onchange="toggleSource('${esc(s.id)}', this.checked)">
                    </label>
                </td>
                <td class="text-xs text-muted">${s.last_fetched_at ? timeAgo(s.last_fetched_at) : 'Never'}</td>
                <td>
                    <button class="btn-icon" onclick="openEditSource('${esc(s.id)}')" title="Edit">&#9998;</button>
                    <button class="btn-icon btn-icon-danger" onclick="openDeleteSource('${esc(s.id)}', '${esc(s.name)}')" title="Remove">&#10005;</button>
                </td>
            </tr>
        `).join('');

        $('#sources-table').innerHTML = `
            <table class="data-table">
                <thead><tr>
                    <th>Name</th><th>URL</th><th>Type</th><th>Enabled</th><th>Last Fetched</th><th></th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    // Wire search
    document.addEventListener('DOMContentLoaded', () => {
        const srcSearch = document.getElementById('sources-search');
        if (srcSearch) srcSearch.addEventListener('input', () => renderSourcesTable(allSources));
    });

    // Toggle enable/disable
    window.toggleSource = async function(id, enable) {
        try {
            const url = enable ? API.sourceEnable(id) : API.sourceDisable(id);
            await postJSON(url, {});
            showToast(enable ? 'Source enabled' : 'Source disabled');
            tabLoaded.settings = false; // invalidate settings source health
        } catch (e) {
            showToast('Could not update source: ' + e.message, 'error');
            loadSources(); // revert toggle visually
        }
    };

    // Auto-extract domain from URL
    function autoExtractDomain() {
        const urlVal = $('#src-url').value;
        try {
            const hostname = new URL(urlVal).hostname;
            $('#src-domain-preview').textContent = 'Domain: ' + hostname;
            return hostname;
        } catch {
            $('#src-domain-preview').textContent = '';
            return '';
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const srcUrl = document.getElementById('src-url');
        if (srcUrl) srcUrl.addEventListener('input', autoExtractDomain);
    });

    // Open Add Source modal
    window.openAddSource = function() {
        $('#source-modal-title').textContent = 'Add Source';
        $('#source-save-btn').textContent = 'Add Source';
        $('#source-save-btn').onclick = saveSource;
        $('#src-id').value = '';
        $('#src-name').value = '';
        $('#src-url').value = '';
        $('#src-type').value = 'rss';
        $('#src-domain-preview').textContent = '';
        $('#source-modal').showModal();
    };

    // Open Edit Source modal
    window.openEditSource = function(id) {
        const src = allSources.find(s => s.id === id);
        if (!src) return;
        $('#source-modal-title').textContent = 'Edit Source';
        $('#source-save-btn').textContent = 'Save Changes';
        $('#source-save-btn').onclick = saveSource;
        $('#src-id').value = src.id;
        $('#src-name').value = src.name;
        $('#src-url').value = src.url || '';
        $('#src-type').value = src.source_type;
        autoExtractDomain();
        $('#source-modal').showModal();
    };

    // Save (create or update)
    window.saveSource = async function() {
        const id = $('#src-id').value;
        const name = $('#src-name').value.trim();
        const url = $('#src-url').value.trim();
        const source_type = $('#src-type').value;
        const priority = 3;
        const trust_level = 'standard';

        if (!name || !url) { showToast('Name and URL are required', 'error'); return; }
        const domain = autoExtractDomain();
        if (!domain) { showToast('Enter a valid URL', 'error'); return; }

        const parser_id = source_type === 'rss' ? 'rss_generic' : 'newsapi';
        const body = { name, url, domain, source_type, parser_id, priority, trust_level };

        await withLoading($('#source-save-btn'), 'Saving...', async () => {
            try {
                if (id) {
                    await putJSON(API.sourceById(id), body);
                    showToast('Source updated');
                } else {
                    await postJSON(API.sources, body);
                    showToast('Source added');
                }
                $('#source-modal').close();
                tabLoaded.settings = false;
                await loadSources();
            } catch (e) {
                showToast('Could not save source: ' + e.message, 'error');
            }
        });
    };

    // Delete source
    window.openDeleteSource = function(id, name) {
        $('#delete-source-id').value = id;
        $('#delete-source-name').textContent = name;
        $('#delete-source-modal').showModal();
    };

    window.confirmDeleteSource = async function() {
        const id = $('#delete-source-id').value;
        if (!id) return;
        await withLoading($('#delete-source-btn'), 'Removing...', async () => {
            try {
                await deleteJSON(API.sourceById(id));
                $('#delete-source-modal').close();
                showToast('Source removed');
                tabLoaded.settings = false;
                await loadSources();
            } catch (e) {
                showToast('Could not remove source: ' + e.message, 'error');
            }
        });
    };

    // Quick Add presets
    window.openQuickAddSources = function() {
        const existingDomains = new Set(allSources.map(s => s.domain));
        const html = SOURCE_PRESETS.map(p => {
            const added = existingDomains.has(p.domain);
            return `
                <div class="quickadd-item" style="display:flex;align-items:center;justify-content:space-between;padding:0.6rem 0;border-bottom:1px solid var(--border);">
                    <div>
                        <strong class="text-sm">${esc(p.name)}</strong>
                        <div class="text-xs text-muted">${esc(p.domain)}</div>
                    </div>
                    ${added
                        ? '<span class="text-xs" style="color:var(--success);">&#10003; Added</span>'
                        : `<button class="btn btn-outline btn-sm" onclick="quickAddSource(this, ${SOURCE_PRESETS.indexOf(p)})">Add</button>`
                    }
                </div>`;
        }).join('');
        $('#quickadd-list').innerHTML = html;
        $('#quickadd-modal').showModal();
    };

    window.quickAddSource = async function(btn, index) {
        const preset = SOURCE_PRESETS[index];
        await withLoading(btn, 'Adding...', async () => {
            try {
                await postJSON(API.sources, preset);
                btn.outerHTML = '<span class="text-xs" style="color:var(--success);">&#10003; Added</span>';
                showToast(preset.name + ' added');
                tabLoaded.settings = false;
                tabLoaded.sources = false;
                // Refresh sources in background
                const data = await fetchJSON(API.sources);
                allSources = data;
            } catch (e) {
                showToast('Could not add source: ' + e.message, 'error');
            }
        });
    };

    // ================================================================
    // Phase 9I — Operator panel (factor overrides, relationships,
    //                           reconcile, backfill)
    // ================================================================
    //
    // Everything in this block is a thin UI on top of the Phase 9H
    // /api/v1/operator/* endpoints.  The panel is portfolio-scoped via
    // _pq() so switching portfolios automatically refreshes what the
    // operator sees.  Source-lane protection (seed/default/ai_inferred
    // are read-only) is enforced server-side AND mirrored in the UI
    // so the buttons that would 409 are never rendered.

    const _OP_SOURCE_STYLE = {
        manual:       { label: 'manual',      cls: 'op-src-manual',   editable: true  },
        seed:         { label: 'seed',        cls: 'op-src-seed',     editable: false },
        ai_inferred:  { label: 'ai-inferred', cls: 'op-src-ai',       editable: false },
        default:      { label: 'default',     cls: 'op-src-default',  editable: false },
        zero:         { label: 'zero',        cls: 'op-src-zero',     editable: false },
    };

    function _opSourceBadge(source) {
        const style = _OP_SOURCE_STYLE[source] || { label: source || 'unknown', cls: 'op-src-default' };
        return `<span class="op-source-badge ${style.cls}">${esc(style.label)}</span>`;
    }

    let _opFactorTaxonomyCache = null;
    let _opHoldingsCache = [];
    let _opFactorRowsCache = [];
    let _opRelRowsCache = [];

    async function _opLoadFactorTaxonomy() {
        if (_opFactorTaxonomyCache) return _opFactorTaxonomyCache;
        try {
            const rows = await fetchJSON(API.opFactorTaxonomy);
            _opFactorTaxonomyCache = Array.isArray(rows) ? rows : [];
        } catch (e) {
            _opFactorTaxonomyCache = [];
        }
        return _opFactorTaxonomyCache;
    }

    // Phase 9N — compute a grounded "next step" hint from the stats
    // returned by a reconcile / backfill / manual-edit action.  Pure
    // function, mirrors the backend ``build_operator_maintenance_action``
    // contract on the client side so we don't have to round-trip the
    // server for a one-line text.
    function _opMaintenanceHint(action, stats, manualEditType) {
        if (action === 'reconcile' && stats) {
            const changed = (stats.created || 0) + (stats.updated || 0);
            const pruned = stats.pruned || 0;
            if (changed === 0 && pruned === 0) return null;
            return `Consider running backfill to apply these ${changed + pruned} seed change${changed + pruned !== 1 ? 's' : ''} to recent events.`;
        }
        if (action === 'backfill' && stats) {
            const linksAdded = stats.links_added || 0;
            const mfeAdded = stats.mfe_added || 0;
            const failed = stats.events_failed || 0;
            if (linksAdded === 0 && mfeAdded === 0 && failed === 0) {
                return 'No new links landed — historical data was already consistent.';
            }
            if (failed > 0) {
                return `${failed} event${failed !== 1 ? 's' : ''} failed — inspect the audit log before retrying.`;
            }
            return `Open the intelligence overview to see the updated posture (${linksAdded} new link${linksAdded !== 1 ? 's' : ''}, ${mfeAdded} factor row${mfeAdded !== 1 ? 's' : ''}).`;
        }
        if (action === 'manual_edit') {
            return 'Run backfill to apply this to recent events (7-day window).';
        }
        return null;
    }

    function _opShowLastResult(title, body, tone, nextStep) {
        const el = $('#op-last-result');
        if (!el) return;
        el.className = 'op-last-result op-last-result-' + (tone || 'info');
        // Phase 9L: include a local timestamp + audit-trail hint on
        // every "ok" result so the operator has visible proof the
        // action was recorded.  Busy / error states skip the hint.
        //
        // Phase 9N: optional ``nextStep`` line renders as a subtle
        // "Next step" row above the footer — grounded in real stats
        // returned by the action, not invented.
        const now = new Date();
        const timestamp = now.toLocaleTimeString(undefined, {
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        });
        const wantsAuditHint = tone === 'ok';
        const footer = wantsAuditHint
            ? `<div class="op-last-result-footer">
                   <span>${esc(timestamp)}</span>
                   <span class="op-audit-hint" title="Every operator action is written to the audit log">
                       &#10003; Saved with audit trail
                   </span>
               </div>`
            : `<div class="op-last-result-footer">
                   <span>${esc(timestamp)}</span>
               </div>`;
        const nextStepBlock = nextStep
            ? `<div class="op-last-result-next-step"><span class="op-next-step-label">Next step:</span> ${esc(nextStep)}</div>`
            : '';
        el.innerHTML =
            `<strong>${esc(title)}</strong>`
            + `<div class="text-xs op-last-result-body">${body}</div>`
            + nextStepBlock
            + footer;
        el.hidden = false;
    }

    // Phase 9L — tiny helper to surface 429 rate-limit responses in
    // a friendly toast.  Mentions the bucket + retry timing when the
    // server provides them (see src/api/middleware.py :: _classify_request).
    // Deliberately throttled so a burst of 429s doesn't spam the user:
    // one toast per second window, per bucket.
    const _rateLimitToastWindowMs = 4000;
    const _rateLimitLastToast = { _default: 0 };
    function _surfaceRateLimitToast(err) {
        const now = Date.now();
        const key = err.bucket || '_default';
        if (now - (_rateLimitLastToast[key] || 0) < _rateLimitToastWindowMs) {
            return;  // throttled
        }
        _rateLimitLastToast[key] = now;
        const retryBit = err.retryAfter ? ` — retry in ~${err.retryAfter}s` : '';
        const bucketBit = err.bucket ? ` [${err.bucket}]` : '';
        const limitBit = err.limitPerMinute ? ` (max ${err.limitPerMinute}/min)` : '';
        showToast(
            `Rate limit reached${bucketBit}${limitBit}${retryBit}`,
            'warning',
        );
    }

    // ---- Factor sensitivities ------------------------------------

    async function loadOperatorFactorSensitivities() {
        const el = $('#op-factor-table');
        if (!el) return;
        el.innerHTML = '<div class="spinner">Loading factor sensitivities...</div>';

        const factorFilter = $('#op-factor-filter')?.value || '';
        const params = new URLSearchParams({ portfolio_id: _activePortfolioId });
        if (factorFilter) params.set('factor', factorFilter);

        try {
            const rows = await fetchJSON(`${API.opFactorSensitivities}?${params.toString()}`);
            _opFactorRowsCache = Array.isArray(rows) ? rows : [];
            renderOperatorFactorTable(_opFactorRowsCache);
        } catch (e) {
            el.innerHTML = renderError('factor sensitivities: ' + e.message);
        }
    }

    function renderOperatorFactorTable(rows) {
        const el = $('#op-factor-table');
        if (!el) return;
        if (!rows.length) {
            el.innerHTML = `<div class="empty-state op-empty"><p class="text-sm text-muted">No holdings in this portfolio — add a holding first.</p></div>`;
            return;
        }

        const body = rows.map(r => {
            const style = _OP_SOURCE_STYLE[r.source] || _OP_SOURCE_STYLE.default;
            const canEdit = true; // every row is editable (create / update / delete override)
            const editLabel = r.source === 'manual' ? 'Edit' : 'Override';
            const deleteBtn = r.source === 'manual'
                ? `<button class="btn-icon btn-icon-danger" data-op="op-factor-delete" data-id="${esc(r.override_id || '')}" title="Delete override">&#10005;</button>`
                : '';
            // Phase 9O — provenance hint.  Manual rows get a subtle
            // "edited <time ago>" line under the timestamp; sector/
            // zero defaults get a "default" hint instead so the
            // operator can distinguish a deliberate default from a
            // missing row at a glance.
            let provenance = '';
            if (r.source === 'manual' && r.updated_at) {
                provenance = `<div class="op-provenance op-provenance-manual" title="Manual override">edited ${esc(timeAgo(r.updated_at) || '')}</div>`;
            } else if (r.source === 'default') {
                provenance = `<div class="op-provenance op-provenance-default" title="Sector default">sector default</div>`;
            } else if (r.source === 'zero') {
                provenance = `<div class="op-provenance op-provenance-default" title="No prior">no prior</div>`;
            }
            return `
                <tr data-holding-id="${esc(r.holding_id)}" data-factor="${esc(r.factor)}" data-source="${esc(r.source || '')}">
                    <td class="text-mono">${esc(r.ticker)}</td>
                    <td>${esc(r.factor_label || r.factor)}</td>
                    <td class="num">${Number(r.effective_value).toFixed(2)}</td>
                    <td>${_opSourceBadge(r.source)}${provenance}</td>
                    <td class="num">${r.override_value != null ? Number(r.override_value).toFixed(2) : '<span class="text-muted">—</span>'}</td>
                    <td class="text-xs text-muted">${r.updated_at ? formatDate(r.updated_at) : '—'}</td>
                    <td>
                        <button class="btn-icon" data-op="op-factor-edit" data-holding-id="${esc(r.holding_id)}" data-factor="${esc(r.factor)}" title="${editLabel} override">&#9998;</button>
                        ${deleteBtn}
                    </td>
                </tr>
            `;
        }).join('');

        el.innerHTML = `
            <div class="table-wrap">
                <table class="data-table op-factor-table">
                    <thead><tr>
                        <th>Ticker</th><th>Factor</th><th class="num">Effective</th><th>Source</th><th class="num">Override</th><th>Updated</th><th style="width:70px;"></th>
                    </tr></thead>
                    <tbody>${body}</tbody>
                </table>
            </div>`;
    }

    async function _opOpenFactorModal(holdingId, factor) {
        const row = _opFactorRowsCache.find(r => r.holding_id === holdingId && r.factor === factor);
        if (!row) { showToast('Row not found', 'error'); return; }

        $('#op-factor-modal-title').textContent = row.source === 'manual'
            ? `Edit override · ${row.ticker}`
            : `New override · ${row.ticker}`;
        $('#op-factor-modal-holding').textContent = `${row.ticker} · ${row.holding_id.slice(0, 8)}…`;
        $('#op-factor-modal-factor').textContent = `${row.factor_label || row.factor} (${row.factor})`;
        $('#op-factor-sensitivity').value = row.override_value != null
            ? Number(row.override_value).toFixed(2)
            : Number(row.effective_value).toFixed(2);
        $('#op-factor-reason').value = '';
        $('#op-factor-modal-current').innerHTML = row.source === 'manual'
            ? `Currently overriding the ${row.sector || 'sector'} default. Effective value: <code>${Number(row.effective_value).toFixed(2)}</code>.`
            : `No override yet. The propagator currently uses the <code>${row.source}</code> value <code>${Number(row.effective_value).toFixed(2)}</code>.`;

        const deleteBtn = $('#op-factor-delete-btn');
        if (row.source === 'manual' && row.override_id) {
            deleteBtn.style.display = '';
            deleteBtn.onclick = async () => {
                if (!confirm(`Delete manual override for ${row.ticker} · ${row.factor}? The system will fall back to the sector default.`)) return;
                try {
                    await deleteJSON(`${API.opFactorOverrideById(row.override_id)}?reason=${encodeURIComponent($('#op-factor-reason').value || 'ui delete')}`);
                    $('#op-factor-modal').close();
                    showToast(`Override cleared for ${row.ticker}/${row.factor}`);
                    _opShowLastResult(
                        'Override deleted',
                        `${esc(row.ticker)} · ${esc(row.factor)} reverted to ${esc(row.source === 'manual' ? 'default' : row.source)} value.`,
                        'ok',
                    );
                    await loadOperatorFactorSensitivities();
                    loadOperatorRecentActions();
                } catch (e) {
                    showToast('Delete failed: ' + e.message, 'error');
                }
            };
        } else {
            deleteBtn.style.display = 'none';
            deleteBtn.onclick = null;
        }

        $('#op-factor-save-btn').onclick = async () => {
            const sensitivity = Number($('#op-factor-sensitivity').value);
            if (!Number.isFinite(sensitivity) || sensitivity < -1 || sensitivity > 1) {
                showToast('Sensitivity must be between -1.0 and 1.0', 'error');
                return;
            }
            try {
                const result = await postJSON(API.opFactorOverrides, {
                    holding_id: row.holding_id,
                    factor: row.factor,
                    sensitivity,
                    reason: $('#op-factor-reason').value || null,
                });
                $('#op-factor-modal').close();
                showToast(`Override saved for ${row.ticker}/${row.factor}`);
                _opShowLastResult(
                    'Override saved',
                    `${esc(row.ticker)} · ${esc(row.factor)} = <code>${Number(result.sensitivity).toFixed(2)}</code> (source <code>${esc(result.source)}</code>)`,
                    'ok',
                    _opMaintenanceHint('manual_edit'),
                );
                await loadOperatorFactorSensitivities();
                loadOperatorRecentActions();  // Phase 9O — refresh readback
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
            }
        };

        $('#op-factor-modal').showModal();
    }

    async function _opDeleteFactorOverride(overrideId) {
        if (!overrideId) return;
        if (!confirm('Delete this manual factor override? The system will fall back to the sector default.')) return;
        try {
            await deleteJSON(`${API.opFactorOverrideById(overrideId)}?reason=${encodeURIComponent('ui quick delete')}`);
            showToast('Override deleted');
            _opShowLastResult('Override deleted', `override <code>${esc(overrideId.slice(0, 8))}…</code> removed`, 'ok');
            await loadOperatorFactorSensitivities();
            loadOperatorRecentActions();  // Phase 9O — refresh readback
        } catch (e) {
            showToast('Delete failed: ' + e.message, 'error');
        }
    }

    // ---- Relationships -------------------------------------------

    async function loadOperatorRelationships() {
        const el = $('#op-rel-table');
        if (!el) return;
        el.innerHTML = '<div class="spinner">Loading relationships...</div>';

        const sourceFilter = $('#op-rel-source-filter')?.value || '';
        const params = new URLSearchParams({ portfolio_id: _activePortfolioId });
        if (sourceFilter) params.set('source', sourceFilter);

        try {
            const rows = await fetchJSON(`${API.opRelationships}?${params.toString()}`);
            _opRelRowsCache = Array.isArray(rows) ? rows : [];
            renderOperatorRelationshipTable(_opRelRowsCache);
        } catch (e) {
            el.innerHTML = renderError('relationships: ' + e.message);
        }
    }

    function renderOperatorRelationshipTable(rows) {
        const el = $('#op-rel-table');
        if (!el) return;
        if (!rows.length) {
            el.innerHTML = `
                <div class="empty-state op-empty">
                    <p class="text-sm text-muted">
                        No relationship rows for this portfolio. Seed rows come from
                        <code>config/relationships.yaml</code> — add one there and run
                        Reconcile, or add a manual row here.
                    </p>
                </div>`;
            return;
        }

        const body = rows.map(r => {
            const editable = r.source === 'manual';
            const relatedCell = r.related_ticker
                ? `<span class="text-mono">${esc(r.related_ticker)}</span>${r.related_name ? ` <span class="text-xs text-muted">${esc(r.related_name)}</span>` : ''}`
                : r.related_entity_key
                    ? `<span class="text-mono text-xs">${esc(r.related_entity_key)}</span>${r.related_name ? ` <span class="text-xs text-muted">${esc(r.related_name)}</span>` : ''}`
                    : `<span class="text-muted">—</span>`;
            const actions = editable
                ? `
                    <button class="btn-icon" data-op="op-rel-edit" data-id="${esc(r.id)}" title="Edit manual row">&#9998;</button>
                    <button class="btn-icon btn-icon-danger" data-op="op-rel-delete" data-id="${esc(r.id)}" title="Delete manual row">&#10005;</button>
                `
                : `<span class="op-rel-locked" title="Non-manual rows are read-only">&#128274;</span>`;
            // Phase 9O — provenance hint.  Seed rows read-only with
            // a "YAML-backed" micro-label; manual rows show a
            // "edited <time ago>" line so the operator can tell at a
            // glance which rows they own.
            let provenance = '';
            if (r.source === 'manual' && r.updated_at) {
                provenance = `<div class="op-provenance op-provenance-manual" title="Manual entry">edited ${esc(timeAgo(r.updated_at) || '')}</div>`;
            } else if (r.source === 'seed') {
                provenance = `<div class="op-provenance op-provenance-seed" title="Loaded from config/relationships.yaml">YAML-backed</div>`;
            } else if (r.source === 'ai_inferred') {
                provenance = `<div class="op-provenance op-provenance-ai" title="Inferred by a Phase 9E agent">agent-inferred</div>`;
            }
            return `
                <tr data-rel-id="${esc(r.id)}" data-source="${esc(r.source || '')}">
                    <td class="text-mono">${esc(r.ticker)}</td>
                    <td class="text-xs text-muted">${esc(r.portfolio_id)}</td>
                    <td><span class="badge badge-muted">${esc(r.relationship_type)}</span></td>
                    <td>${relatedCell}</td>
                    <td class="num">${Number(r.strength).toFixed(2)}</td>
                    <td>${_opSourceBadge(r.source)}${provenance}</td>
                    <td class="text-xs text-muted">${r.updated_at ? formatDate(r.updated_at) : '—'}</td>
                    <td>${actions}</td>
                </tr>
            `;
        }).join('');

        el.innerHTML = `
            <div class="table-wrap">
                <table class="data-table op-rel-table">
                    <thead><tr>
                        <th>Holding</th><th>Portfolio</th><th>Type</th><th>Related</th><th class="num">Strength</th><th>Source</th><th>Updated</th><th style="width:80px;"></th>
                    </tr></thead>
                    <tbody>${body}</tbody>
                </table>
            </div>`;
    }

    function _opOpenRelModal(mode, existing) {
        $('#op-rel-modal-title').textContent = mode === 'edit' ? 'Edit Manual Relationship' : 'Add Manual Relationship';

        const sel = $('#op-rel-holding');
        sel.innerHTML = (_opHoldingsCache || [])
            .map(h => `<option value="${esc(h.id)}">${esc(h.ticker)}</option>`)
            .join('');

        if (mode === 'edit' && existing) {
            // In edit mode, holding + relationship_type + identity tuple are immutable
            sel.value = existing.holding_id;
            sel.disabled = true;
            $('#op-rel-type').value = existing.relationship_type;
            $('#op-rel-type').disabled = true;
            $('#op-rel-related-ticker').value = existing.related_ticker || '';
            $('#op-rel-related-ticker').disabled = true;
            $('#op-rel-related-entity-key').value = existing.related_entity_key || '';
            $('#op-rel-related-entity-key').disabled = true;
            $('#op-rel-related-name').value = existing.related_name || '';
            $('#op-rel-strength').value = Number(existing.strength || 0.5).toFixed(2);
            $('#op-rel-description').value = existing.description || '';
            $('#op-rel-reason').value = '';
        } else {
            sel.disabled = false;
            $('#op-rel-type').disabled = false;
            $('#op-rel-type').value = 'supplier';
            $('#op-rel-related-ticker').disabled = false;
            $('#op-rel-related-ticker').value = '';
            $('#op-rel-related-entity-key').disabled = false;
            $('#op-rel-related-entity-key').value = '';
            $('#op-rel-related-name').value = '';
            $('#op-rel-strength').value = '0.50';
            $('#op-rel-description').value = '';
            $('#op-rel-reason').value = '';
        }

        $('#op-rel-save-btn').onclick = async () => {
            const body = {
                holding_id: sel.value,
                relationship_type: $('#op-rel-type').value,
                related_ticker: ($('#op-rel-related-ticker').value || '').trim() || null,
                related_entity_key: ($('#op-rel-related-entity-key').value || '').trim() || null,
                related_name: ($('#op-rel-related-name').value || '').trim() || null,
                strength: Number($('#op-rel-strength').value),
                description: ($('#op-rel-description').value || '').trim() || null,
                reason: ($('#op-rel-reason').value || '').trim() || null,
            };
            if (!body.related_ticker && !body.related_entity_key) {
                showToast('Provide related_ticker or related_entity_key', 'error');
                return;
            }
            if (!Number.isFinite(body.strength) || body.strength < 0 || body.strength > 1) {
                showToast('Strength must be between 0.0 and 1.0', 'error');
                return;
            }
            try {
                if (mode === 'edit' && existing) {
                    await putJSON(API.opRelationshipById(existing.id), {
                        strength: body.strength,
                        related_name: body.related_name,
                        description: body.description,
                        reason: body.reason,
                    });
                    _opShowLastResult(
                        'Relationship updated',
                        `manual row <code>${esc(existing.id.slice(0, 8))}…</code> updated`,
                        'ok',
                        _opMaintenanceHint('manual_edit'),
                    );
                } else {
                    const created = await postJSON(API.opRelationships, body);
                    _opShowLastResult(
                        'Manual relationship created',
                        `${esc(created.ticker)} &rarr; ${esc(created.related_ticker || created.related_entity_key || '')} (<code>${esc(created.relationship_type)}</code>)`,
                        'ok',
                        _opMaintenanceHint('manual_edit'),
                    );
                }
                $('#op-rel-modal').close();
                showToast('Relationship saved');
                await loadOperatorRelationships();
                loadOperatorRecentActions();
            } catch (e) {
                showToast('Save failed: ' + e.message, 'error');
            }
        };

        $('#op-rel-modal').showModal();
    }

    async function _opDeleteRelationship(relId) {
        const row = _opRelRowsCache.find(r => r.id === relId);
        if (!row) return;
        if (row.source !== 'manual') {
            showToast(`Cannot delete ${row.source} rows. Only manual rows are editable here.`, 'error');
            return;
        }
        const label = row.related_ticker || row.related_entity_key || row.relationship_type;
        if (!confirm(`Delete manual relationship ${row.ticker} → ${label}? This cannot be undone.`)) return;
        try {
            await deleteJSON(`${API.opRelationshipById(relId)}?reason=${encodeURIComponent('ui delete')}`);
            showToast('Relationship deleted');
            _opShowLastResult('Manual relationship deleted', `${esc(row.ticker)} &rarr; ${esc(label)} removed`, 'ok');
            await loadOperatorRelationships();
            loadOperatorRecentActions();
        } catch (e) {
            showToast('Delete failed: ' + e.message, 'error');
        }
    }

    // ---- Phase 9L — Live action status, busy buttons, 409/429 UX ---

    // Action-chip labels + styling.  The server-reported lock state
    // (from /api/v1/operator/actions/status) feeds directly into this
    // table.  The "locally running" state is a UI-only superset — it
    // triggers instantly on button click so the user sees immediate
    // feedback even before the first status poll fires.
    const _OP_CHIP_STYLE = {
        idle:       { label: 'Idle',     cls: 'op-action-chip-idle' },
        running:    { label: 'Running…', cls: 'op-action-chip-running' },
        unknown:    { label: 'Unknown',  cls: 'op-action-chip-idle' },
    };

    // Local in-flight flags so a single click immediately disables
    // the button AND flips the chip without waiting for the next
    // status poll response.  The poller confirms / corrects these.
    let _opReconcileLocalRunning = false;
    let _opBackfillLocalRunning = false;

    // Server-reported in-flight state (last poll result).  Cached so
    // the DOM renderer can merge it with the local state without a
    // network round-trip per click.
    let _opReconcileServerRunning = false;
    let _opBackfillServerRunning = false;

    // Status poller lifecycle — set while the Settings tab is active.
    let _opStatusTimer = null;
    const _OP_STATUS_POLL_MS = 4000;

    function _opIsReconcileRunning() {
        return _opReconcileLocalRunning || _opReconcileServerRunning;
    }
    function _opIsBackfillRunning() {
        return _opBackfillLocalRunning || _opBackfillServerRunning;
    }

    function _opUpdateActionChipUI() {
        const reconcileRunning = _opIsReconcileRunning();
        const backfillRunning = _opIsBackfillRunning();

        const recChip = $('#op-reconcile-chip');
        if (recChip) {
            const s = reconcileRunning ? _OP_CHIP_STYLE.running : _OP_CHIP_STYLE.idle;
            recChip.className = 'op-action-chip ' + s.cls;
            const label = recChip.querySelector('.op-action-chip-label');
            if (label) label.textContent = s.label;
        }
        const bfChip = $('#op-backfill-chip');
        if (bfChip) {
            const s = backfillRunning ? _OP_CHIP_STYLE.running : _OP_CHIP_STYLE.idle;
            bfChip.className = 'op-action-chip ' + s.cls;
            const label = bfChip.querySelector('.op-action-chip-label');
            if (label) label.textContent = s.label;
        }

        const recBtn = $('#op-reconcile-btn');
        if (recBtn) {
            if (reconcileRunning) {
                recBtn.disabled = true;
                recBtn.classList.add('op-btn-busy');
                recBtn.textContent = 'Running…';
            } else {
                recBtn.disabled = false;
                recBtn.classList.remove('op-btn-busy');
                recBtn.textContent = 'Run Reconcile';
            }
        }
        const bfBtn = $('#op-backfill-btn');
        if (bfBtn) {
            if (backfillRunning) {
                bfBtn.disabled = true;
                bfBtn.classList.add('op-btn-busy');
                bfBtn.textContent = 'Running…';
            } else {
                bfBtn.disabled = false;
                bfBtn.classList.remove('op-btn-busy');
                bfBtn.textContent = 'Run Backfill';
            }
        }
    }

    async function _opPollActionsStatus() {
        try {
            const data = await fetchJSON(API.opActionsStatus);
            if (!data) return;
            _opReconcileServerRunning = !!(data.reconcile && data.reconcile.in_progress);
            _opBackfillServerRunning = !!(data.backfill && data.backfill.in_progress);
        } catch (_e) {
            // Swallow poll errors — the chip falls back to "Idle" via
            // the local flags and the next successful poll corrects it.
            _opReconcileServerRunning = false;
            _opBackfillServerRunning = false;
        }
        _opUpdateActionChipUI();
    }

    function _opStartStatusPolling() {
        if (_opStatusTimer != null) return;  // already polling
        _opPollActionsStatus();               // fire one immediately
        _opStatusTimer = setInterval(_opPollActionsStatus, _OP_STATUS_POLL_MS);
    }

    function _opStopStatusPolling() {
        if (_opStatusTimer != null) {
            clearInterval(_opStatusTimer);
            _opStatusTimer = null;
        }
    }

    // ---- Reconcile + Backfill (refactored) ------------------------

    async function _opRunReconcile() {
        const statusLine = $('#op-reconcile-status');
        const prune = $('#op-reconcile-prune')?.checked ?? true;

        // Local guard: don't even submit if the chip already says running.
        // The click might have landed before the button's disabled state
        // propagated.
        if (_opIsReconcileRunning()) {
            _opShowLastResult(
                'Reconcile already running',
                'Wait for the current reconcile to finish before starting another.',
                'busy',
            );
            return;
        }

        if (prune && !confirm('Run seed reconcile with prune enabled?\n\nThis removes seed rows no longer in config/relationships.yaml.\nManual and AI-inferred rows are NOT touched.')) {
            return;
        }

        _opReconcileLocalRunning = true;
        _opUpdateActionChipUI();
        if (statusLine) statusLine.textContent = '';

        try {
            const resp = await postJSON(`${API.opRelationshipsReconcile}?prune=${prune}`, {});
            const s = resp.stats || {};
            if (statusLine) {
                statusLine.textContent = `created ${s.created || 0} · updated ${s.updated || 0} · unchanged ${s.unchanged || 0} · pruned ${s.pruned || 0} · skipped ${s.skipped_no_holding || 0}`;
            }
            _opShowLastResult(
                'Reconcile complete',
                `loaded=${s.seed_rows_loaded || 0} created=${s.created || 0} updated=${s.updated || 0} unchanged=${s.unchanged || 0} pruned=${s.pruned || 0} skipped_no_holding=${s.skipped_no_holding || 0} skipped_manual_row=${s.skipped_manual_row || 0}`,
                'ok',
                _opMaintenanceHint('reconcile', s),
            );
            await loadOperatorRelationships();
            loadOperatorRecentActions();
        } catch (e) {
            _opHandleOperatorError(e, 'reconcile', statusLine);
        } finally {
            _opReconcileLocalRunning = false;
            // Re-poll immediately so the server view refreshes BEFORE
            // the button state is restored — prevents a gap where the
            // user could click again while the lock is still held.
            await _opPollActionsStatus();
        }
    }

    async function _opRunBackfill() {
        const statusLine = $('#op-backfill-status');
        const window_days = parseInt($('#op-backfill-window')?.value || '7', 10);
        const max_events = parseInt($('#op-backfill-max')?.value || '200', 10);

        if (_opIsBackfillRunning()) {
            _opShowLastResult(
                'Backfill already running',
                'Wait for the current backfill to finish before starting another.',
                'busy',
            );
            return;
        }

        if (!confirm(`Replay the deterministic link pipeline over the last ${window_days} day(s), up to ${max_events} events?\n\nSafe to run — idempotent and bounded.`)) {
            return;
        }

        _opBackfillLocalRunning = true;
        _opUpdateActionChipUI();
        if (statusLine) statusLine.textContent = '';

        try {
            const resp = await postJSON(API.opBackfill, {
                window_days,
                max_events,
                reason: 'ui backfill',
            });
            const s = resp.stats || {};
            if (statusLine) {
                statusLine.textContent = `scanned ${s.events_scanned || 0} · replayed ${s.events_replayed || 0} · links+${s.links_added || 0} · mfe+${s.mfe_added || 0}`;
            }
            _opShowLastResult(
                'Backfill complete',
                `window=${s.window_days || window_days}d scanned=${s.events_scanned || 0} replayed=${s.events_replayed || 0} failed=${s.events_failed || 0} links_added=${s.links_added || 0} mfe_added=${s.mfe_added || 0}`,
                'ok',
                _opMaintenanceHint('backfill', s),
            );
            await loadOperatorFactorSensitivities();
            loadOperatorRecentActions();
        } catch (e) {
            _opHandleOperatorError(e, 'backfill', statusLine);
        } finally {
            _opBackfillLocalRunning = false;
            await _opPollActionsStatus();
        }
    }

    // Centralised error handler for reconcile / backfill actions.
    // Phase 9L: tell apart three cases and render each honestly:
    //   1. 409 "in progress"  → friendly busy state (not red)
    //   2. 429 rate limit     → friendly throttle state + retry hint
    //   3. anything else      → generic failure (unchanged behaviour)
    function _opHandleOperatorError(e, actionLabel, statusLine) {
        const actionTitle = actionLabel === 'reconcile' ? 'Reconcile' : 'Backfill';

        if (e && e.isInProgress) {
            // Server says the lock is already held.  Mirror the state
            // into the local flag so the chip stays "Running…" until
            // the next poll reports idle.
            if (actionLabel === 'reconcile') {
                _opReconcileServerRunning = true;
            } else {
                _opBackfillServerRunning = true;
            }
            _opUpdateActionChipUI();
            if (statusLine) statusLine.textContent = '';
            _opShowLastResult(
                `${actionTitle} already running`,
                esc(e.message || 'An instance is already running — wait for it to finish.'),
                'busy',
            );
            return;
        }

        if (e && e.isRateLimit) {
            if (statusLine) statusLine.textContent = '';
            const retryMsg = e.retryAfter
                ? ` Retry in ~${e.retryAfter}s.`
                : '';
            _opShowLastResult(
                `${actionTitle} rate-limited`,
                `The API rate limit for <code>${esc(e.bucket || 'mutation')}</code> (${esc(String(e.limitPerMinute || '?'))}/min) was exceeded.${retryMsg}`,
                'busy',
            );
            _surfaceRateLimitToast(e);
            return;
        }

        // Generic failure — preserve the pre-9L behaviour.
        if (statusLine) statusLine.textContent = '';
        _opShowLastResult(
            `${actionTitle} failed`,
            esc(e && e.message ? e.message : String(e)),
            'err',
        );
        showToast(`${actionTitle} failed: ${e && e.message ? e.message : String(e)}`, 'error');
    }

    // ---- Phase 9O — Recent operator actions readback ---------------
    //
    // Reads /api/v1/audit/recent (a thin shaping wrapper over the
    // existing audit_log table) and renders the top 10 operator-owned
    // mutations + maintenance runs as a compact list under the
    // Maintenance Actions card.  Every row is grounded in real audit
    // data — no local client memory, no synthesis.

    const _OP_RECENT_ENTITY_CLASS = {
        holding_factor_sensitivity: 'op-recent-entity-factor',
        holding_relationship:       'op-recent-entity-rel',
        holding_relationships:      'op-recent-entity-reconcile',
        intelligence_backfill:      'op-recent-entity-backfill',
    };

    function _renderOperatorRecentActions(entries) {
        const el = $('#op-recent-actions');
        if (!el) return;
        const list = Array.isArray(entries) ? entries : [];
        if (!list.length) {
            el.innerHTML = `<div class="op-recent-empty text-sm text-muted">No recent operator actions yet. Mutations will appear here as you use the panel.</div>`;
            return;
        }
        el.innerHTML = `<ul class="op-recent-list">${
            list.map(e => {
                const entityCls = _OP_RECENT_ENTITY_CLASS[e.entity_type] || 'op-recent-entity-default';
                const label = e.entity_type_label || e.entity_type || 'action';
                const reason = e.reason
                    ? `<div class="op-recent-reason"><span class="op-recent-reason-label">Reason:</span> ${esc(e.reason)}</div>`
                    : '';
                const portfolioChip = e.portfolio_id
                    ? `<span class="op-recent-portfolio" title="Portfolio-scoped change">${esc(e.portfolio_id)}</span>`
                    : '';
                // Phase 9Q — attach the structured nav target emitted
                // by the audit recent route so clicking the row jumps
                // to the matching operator sub-section (factors /
                // relationships / maintenance).
                const nav = e.nav_target && typeof e.nav_target === 'object' && e.nav_target.surface
                    ? e.nav_target
                    : null;
                const navAttr = nav ? `data-nav-target="${escAttr(JSON.stringify(nav))}"` : '';
                const jumpAttr = nav ? `data-nav-jump="1"` : '';
                const cursorCls = nav ? 'op-recent-row-clickable' : '';
                return `
                    <li class="op-recent-row ${cursorCls}" data-entity-type="${esc(e.entity_type || '')}" data-action="${esc(e.action || '')}" data-audit-id="${esc(e.id || '')}" ${navAttr} ${jumpAttr} ${nav ? 'tabindex="0" role="button"' : ''}>
                        <div class="op-recent-header">
                            <span class="op-recent-time text-xs text-muted">${formatDate(e.timestamp)}</span>
                            <span class="op-recent-entity ${entityCls}">${esc(label)}</span>
                            ${portfolioChip}
                        </div>
                        <div class="op-recent-title">${esc(e.title || '')}</div>
                        <div class="op-recent-summary text-sm text-muted">${esc(e.summary || '')}</div>
                        ${reason}
                    </li>
                `;
            }).join('')
        }</ul>`;
    }

    async function loadOperatorRecentActions() {
        const el = $('#op-recent-actions');
        if (!el) return;
        const portfolioId = _activePortfolioId;
        try {
            const url = `${API.auditRecent}?limit=10${portfolioId ? `&portfolio_id=${encodeURIComponent(portfolioId)}` : ''}`;
            const data = await fetchJSON(url);
            const list = Array.isArray(data) ? data : (data?.items || []);
            _renderOperatorRecentActions(list);
        } catch (e) {
            el.innerHTML = `<div class="op-recent-error text-sm text-danger">Could not load recent actions: ${esc(e.message || 'unknown error')}</div>`;
        }
    }

    // ---- Operator panel entry point ------------------------------

    async function loadOperatorPanel() {
        // Portfolio identity banner
        const pidEl = $('#op-active-portfolio');
        if (pidEl) {
            const portfolioName = (_portfolioList.find(p => p.id === _activePortfolioId) || {}).name || _activePortfolioId;
            pidEl.textContent = `${portfolioName} (${_activePortfolioId})`;
        }

        // Factor filter options — populated from taxonomy once per session
        const factorSel = $('#op-factor-filter');
        if (factorSel && factorSel.options.length <= 1) {
            const taxonomy = await _opLoadFactorTaxonomy();
            taxonomy.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f.key;
                opt.textContent = f.label;
                factorSel.appendChild(opt);
            });
        }

        // Holdings cache — used to populate the manual relationship modal
        try {
            const holdings = await fetchJSON(_pq(API.holdings));
            _opHoldingsCache = Array.isArray(holdings) ? holdings : (holdings?.items || []);
        } catch (e) {
            _opHoldingsCache = [];
        }

        // Load tables in parallel
        loadOperatorFactorSensitivities();
        loadOperatorRelationships();
        // Phase 9O — compact recent operator actions readback (audit
        // row shaping).  Fire-and-forget so a slow audit query can't
        // block the rest of the panel.
        loadOperatorRecentActions();

        // Phase 9L: start the action-status poller so the chips / button
        // states track the server's reconcile + backfill lock state.
        // The poller is stopped by ``switchTab`` when the user leaves
        // the Settings tab (see below) so we're not burning a poll
        // every 4s in the background when the panel isn't visible.
        _opStartStatusPolling();
    }

    // Event delegation for the operator tables (single listener keeps
    // DOM event cost low and survives table re-renders)
    document.addEventListener('click', (ev) => {
        const target = ev.target.closest('[data-op]');
        if (!target) return;
        const op = target.dataset.op;
        if (op === 'op-factor-edit') {
            _opOpenFactorModal(target.dataset.holdingId, target.dataset.factor);
        } else if (op === 'op-factor-delete') {
            _opDeleteFactorOverride(target.dataset.id);
        } else if (op === 'op-rel-edit') {
            const row = _opRelRowsCache.find(r => r.id === target.dataset.id);
            if (row) _opOpenRelModal('edit', row);
        } else if (op === 'op-rel-delete') {
            _opDeleteRelationship(target.dataset.id);
        }
    });

    // Wire static control handlers once
    document.addEventListener('DOMContentLoaded', () => {
        $('#op-factor-refresh')?.addEventListener('click', () => loadOperatorFactorSensitivities());
        $('#op-factor-filter')?.addEventListener('change', () => loadOperatorFactorSensitivities());
        $('#op-rel-refresh')?.addEventListener('click', () => loadOperatorRelationships());
        $('#op-rel-source-filter')?.addEventListener('change', () => loadOperatorRelationships());
        $('#op-rel-add')?.addEventListener('click', () => _opOpenRelModal('create', null));
        $('#op-reconcile-btn')?.addEventListener('click', () => _opRunReconcile());
        $('#op-backfill-btn')?.addEventListener('click', () => _opRunBackfill());
        // Phase 9O — recent operator actions manual refresh
        $('#op-recent-refresh')?.addEventListener('click', () => loadOperatorRecentActions());
    });

    // Patch the tab loader so Settings loads API key status + health + sources + operator
    tabLoaders.settings = function () {
        loadSettings();
        loadApiKeyStatus();
        loadHealth();
        loadSources();
        loadOperatorPanel();
        loadSavedViews();
    };

    // ================================================================
    // Phase 9U — Saved Analytical Views
    // ================================================================

    function _captureCurrentViewPayload() {
        // Build a NavigationTarget-compatible payload from the current
        // dashboard state (active tab + sub-tab + filters).
        const activeTabLink = document.querySelector('.tab-link.active');
        const tab = activeTabLink ? activeTabLink.dataset.tab : 'portfolio';

        // Map top-level tab → surface
        const tabToSurface = {
            portfolio: 'portfolio',
            intelligence: 'events',  // default intelligence surface
            alerts: 'alerts',
            settings: 'operator',
        };
        let surface = tabToSurface[tab] || 'portfolio';
        let subtab = null;

        // Check for active intelligence sub-tab
        if (tab === 'intelligence') {
            const activeSub = document.querySelector('#tab-intelligence .sub-tab.active');
            if (activeSub) {
                subtab = activeSub.dataset.subtab;
                if (subtab === 'digest') surface = 'digest';
                else surface = 'events';
            }
        }

        // Collect approved filters
        const filters = {};
        if (surface === 'alerts') {
            // Phase 9V/9W — capture active severity + acknowledged filters
            const sv = document.querySelector('#alerts-severity-filter')?.value;
            if (sv) filters.severity = sv;
            const ak = document.querySelector('#alerts-ack-filter')?.value;
            if (ak) filters.ack = ak;
        }
        if (surface === 'operator') {
            const ff = document.querySelector('#op-factor-filter')?.value;
            if (ff) filters.factor = ff;
            const rs = document.querySelector('#op-rel-source-filter')?.value;
            if (rs) filters.source = rs;
            subtab = ff ? 'factors' : (rs ? 'relationships' : null);
        }
        if (surface === 'events' || subtab === 'events') {
            const es = document.querySelector('#events-search')?.value;
            if (es) filters.search = es;
        }

        const payload = {
            surface,
            portfolio_id: _activePortfolioId,
        };
        if (subtab) payload.subtab = subtab;
        if (Object.keys(filters).length) payload.filters = filters;
        return payload;
    }

    async function loadSavedViews() {
        const el = document.querySelector('#saved-views-list');
        if (!el) return;
        try {
            const views = await fetchJSON(
                `${API.savedViews}?portfolio_id=${encodeURIComponent(_activePortfolioId)}`
            );
            const list = Array.isArray(views) ? views : [];
            if (!list.length) {
                el.innerHTML = '<div class="text-sm text-muted" style="padding:0.5rem;">No saved views yet. Navigate to a filtered surface and click "Save current view".</div>';
                return;
            }
            el.innerHTML = `<ul class="saved-views-list">${list.map(v => {
                let payloadJson = '';
                try { payloadJson = JSON.stringify(v.payload); } catch (_) {}
                return `
                    <li class="saved-view-row" data-view-id="${esc(v.id)}">
                        <div class="saved-view-header">
                            <span class="saved-view-name">${esc(v.name)}</span>
                            <span class="text-xs text-muted">${timeAgo(v.updated_at)}</span>
                        </div>
                        <div class="saved-view-description text-xs text-muted">${esc(v.description || v.surface || '')}</div>
                        <div class="saved-view-actions">
                            <button class="btn btn-ghost btn-sm saved-view-restore" data-nav-target="${escAttr(payloadJson)}" data-nav-jump="1">Restore</button>
                            <button class="btn btn-ghost btn-sm copy-link-btn saved-view-copy" data-copy-target="${escAttr(payloadJson)}" title="Copy shareable link">&#128279;</button>
                            <button class="btn btn-ghost btn-sm saved-view-delete" data-view-id="${esc(v.id)}" data-view-name="${esc(v.name)}">Delete</button>
                        </div>
                    </li>
                `;
            }).join('')}</ul>`;
        } catch (e) {
            el.innerHTML = `<div class="text-sm text-danger">Could not load saved views: ${esc(e.message)}</div>`;
        }
    }

    // Phase 9W — build a compact auto-suggested name from the current
    // view state so operators don't have to type from scratch.
    function _autoSuggestViewName(payload) {
        // Mirror the backend's ``describe_view`` logic in a tiny
        // client-side form so the prompt pre-fills a reasonable name.
        const surfaceLabels = {
            alerts:'Alerts', digest:'Digest', events:'News',
            operator:'Operator', portfolio:'Portfolio',
        };
        const label = surfaceLabels[payload.surface] || payload.surface || '';
        const parts = [label];
        const f = payload.filters || {};
        const sevLabels = {
            '':'All','all':'All','critical':'Critical',
            'critical_high':'Critical+High','high':'High+',
            'warning':'Warning+','info':'Info',
        };
        const ackLabels = {'open':'Open','ack':'Acked','':'All'};
        if (f.severity) parts.push(sevLabels[f.severity] || f.severity);
        if (f.ack && f.ack !== 'open') parts.push(ackLabels[f.ack] || f.ack);
        if (f.factor) parts.push(f.factor);
        if (f.source) parts.push(f.source);
        if (f.search) parts.push('search: ' + f.search);
        return parts.join(' · ');
    }

    async function _saveCurrentView() {
        const payload = _captureCurrentViewPayload();
        const suggested = _autoSuggestViewName(payload);
        const name = prompt('Name this view:', suggested);
        if (!name || !name.trim()) return;
        try {
            await postJSON(API.savedViews, {
                portfolio_id: _activePortfolioId,
                name: name.trim(),
                surface: payload.surface,
                payload,
            });
            showToast(`View "${name.trim()}" saved`);
            loadSavedViews();
        } catch (e) {
            showToast('Save failed: ' + e.message, 'error');
        }
    }

    async function _deleteSavedView(viewId, viewName) {
        if (!confirm(`Delete saved view "${viewName}"?`)) return;
        try {
            await deleteJSON(
                `${API.savedViews}/${viewId}?portfolio_id=${encodeURIComponent(_activePortfolioId)}`
            );
            showToast(`View "${viewName}" deleted`);
            loadSavedViews();
        } catch (e) {
            showToast('Delete failed: ' + e.message, 'error');
        }
    }

    // Click delegation for saved view buttons
    document.addEventListener('click', (ev) => {
        const deleteBtn = ev.target.closest('.saved-view-delete');
        if (deleteBtn) {
            ev.preventDefault();
            _deleteSavedView(deleteBtn.dataset.viewId, deleteBtn.dataset.viewName || 'this view');
            return;
        }
    });

    // Update the placeholder based on selected provider
    function updateKeyPlaceholder(role) {
        const providerSelect = document.getElementById('setting-' + role + '-provider');
        const keyInput = document.getElementById('setting-' + role + '-key');
        if (!providerSelect || !keyInput) return;
        keyInput.placeholder = _KEY_PLACEHOLDERS[providerSelect.value] || 'API key...';
    }

    // Phase 6 — test an AI provider (primary or backup).
    //
    // Calls POST /api/v1/settings/test-provider?provider=<chosen> with the
    // provider the user selected in the dropdown, so testing the primary
    // doesn't have to disturb the persisted selection. The endpoint returns
    // the normalized ProviderStatus shape (active / disabled / missing_key /
    // invalid_key / quota_issue / unreachable / misconfigured / error). The
    // UI renders the status with calm wording and dot colours; the raw key
    // never leaves the browser.
    const _STATUS_DOT_CLASS = {
        active:        'status-dot status-ok',
        disabled:      'status-dot status-stopped',
        missing_key:   'status-dot status-stopped',
        invalid_key:   'status-dot status-error',
        quota_issue:   'status-dot status-warn',
        unreachable:   'status-dot status-warn',
        misconfigured: 'status-dot status-warn',
        error:         'status-dot status-error',
    };
    const _STATUS_LABEL = {
        active:        'Active',
        disabled:      'Disabled',
        missing_key:   'Not configured',
        invalid_key:   'Invalid key',
        quota_issue:   'Quota / rate-limit',
        unreachable:   'Unreachable',
        misconfigured: 'Misconfigured',
        error:         'Error',
    };

    window.testProvider = async function (role) {
        const providerSelect = document.getElementById('setting-' + role + '-provider');
        const resultEl = document.getElementById(role + '-test-result');
        const provider = providerSelect?.value || '';
        if (!resultEl) return;
        if (!provider) {
            resultEl.hidden = false;
            resultEl.innerHTML = '<span class="text-xs text-muted">Select a provider first.</span>';
            return;
        }
        resultEl.hidden = false;
        resultEl.innerHTML = '<span class="text-xs text-muted">Testing provider…</span>';
        try {
            const res = await fetch(
                '/api/v1/settings/test-provider?provider=' + encodeURIComponent(provider),
                { method: 'POST', headers: { 'Content-Type': 'application/json' } }
            );
            const data = await res.json();
            if (!res.ok) {
                resultEl.innerHTML = `<span class="text-xs" style="color:var(--color-error,#c44);">${esc(data.detail || 'Test failed.')}</span>`;
                return;
            }
            const dotCls  = _STATUS_DOT_CLASS[data.status] || _STATUS_DOT_CLASS.error;
            const label   = _STATUS_LABEL[data.status] || data.status;
            const detail  = data.message || '';
            const modelTxt = data.model ? ` <span class="text-muted">(model: ${esc(data.model)})</span>` : '';
            resultEl.innerHTML =
                `<span class="${dotCls}" style="display:inline-block;width:0.55rem;height:0.55rem;border-radius:50%;margin-right:0.4rem;"></span>` +
                `<span class="text-xs"><strong>${esc(label)}</strong>${modelTxt} — ${esc(detail)}</span>`;
        } catch (e) {
            resultEl.innerHTML = '<span class="text-xs" style="color:var(--color-error,#c44);">Test failed: ' + esc(e.message) + '</span>';
        }
    };

    // Save provider key (multi-provider)
    window.saveProviderKey = async function (role) {
        const providerSelect = document.getElementById('setting-' + role + '-provider');
        const keyInput = document.getElementById('setting-' + role + '-key');
        const provider = providerSelect?.value;
        const key = (keyInput?.value || '').trim();
        if (!provider) { showToast('Select a provider first.', 'warning'); return; }
        if (!key) { showToast('Please enter an API key.', 'warning'); return; }

        // Basic format validation per provider
        const prefixes = { anthropic: 'sk-ant-', openai: 'sk-', google: 'AIza' };
        const prefix = prefixes[provider];
        if (prefix && !key.startsWith(prefix)) {
            showToast(`${provider} keys typically start with '${prefix}'.`, 'warning');
            return;
        }

        try {
            const res = await fetch('/api/v1/settings/api-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_key: key, provider: provider, role: role }),
            });
            const data = await res.json();
            if (!res.ok) { showToast(data.detail || 'Save failed.', 'error'); return; }
            showToast(data.message, 'success');
            keyInput.value = '';
            _showRestartBanner();
            await loadApiKeyStatus();
        } catch (e) {
            showToast('Failed to save key: ' + e.message, 'error');
        }
    };

    // ================================================================
    // Holding Detail Slide-out
    // ================================================================
    window.openHoldingDetail = async function (holdingId) {
        const panel = $('#holding-detail');
        const overlay = $('#detail-overlay');
        const body = $('#detail-body');
        if (!panel) return;

        panel.classList.add('open');
        overlay.classList.add('open');
        body.innerHTML = '<div class="spinner">Loading...</div>';

        // Phase 9S — wire the copy-link button to the current holding
        const copyBtn = $('#holding-detail-copy-link');
        if (copyBtn) {
            copyBtn.onclick = () => {
                _copyDeepLink({
                    surface: 'portfolio',
                    portfolio_id: _activePortfolioId,
                    entity_type: 'holding',
                    entity_id: holdingId,
                    open_modal: true,
                    highlight_key: 'holding:' + holdingId,
                });
            };
        }

        const h = allHoldings.find(x => x.id === holdingId);
        if (!h) {
            body.innerHTML = '<div class="error-state">Holding not found</div>';
            return;
        }

        const mv = h.market_value || ((h.current_price || 0) * (h.quantity || 0));
        const cost = (h.quantity || 0) * (h.avg_cost_basis || 0);
        const pnl = mv - cost;
        const pnlPct = cost ? (pnl / cost) : 0;

        $('#detail-title').textContent = `${h.ticker}${h.name ? ' — ' + h.name : ''}`;

        // Fetch related data in parallel
        const [events, notes, alerts] = await Promise.all([
            fetchJSON(`${API.events}?ticker=${h.ticker}&limit=10`).catch(() => []),
            fetchJSON(_pq(`${API.analysisNotes}`) + `&ticker=${h.ticker}`).catch(() => []),
            fetchJSON(`${API.alertsActive}?ticker=${h.ticker}`).catch(() => []),
        ]);

        const eventList = ensureArray(events, 'items', 'events');
        const noteList = ensureArray(notes, 'items', 'notes');
        const alertList = ensureArray(alerts, 'items', 'alerts');

        body.innerHTML = `
            <div class="detail-value-card">
                <div class="detail-value-stat">
                    <span class="label">Market Value</span>
                    <span class="value value-lg">${formatCurrency(mv)}</span>
                </div>
                <div class="detail-value-divider"></div>
                <div class="detail-value-stat">
                    <span class="label">P&L</span>
                    <span class="value ${pnlClass(pnl)}">${formatCurrency(pnl)}</span>
                </div>
                <div class="detail-value-stat">
                    <span class="label">P&L %</span>
                    <span class="value ${pnlClass(pnlPct)}">${formatPct(pnlPct)}</span>
                </div>
            </div>

            <div class="detail-section">
                <h4>Position</h4>
                <div class="detail-row"><span class="label">Sector</span><span class="value" style="font-family:inherit">${esc(h.sector || '\u2014')}</span></div>
                <div class="detail-row"><span class="label">Geography</span><span class="value" style="font-family:inherit">${esc(h.geography || '\u2014')}</span></div>
                <div class="detail-row"><span class="label">Shares</span><span class="value">${formatNum(h.quantity, 0)}</span></div>
                <div class="detail-row"><span class="label">Avg Cost</span><span class="value">${formatNum(h.avg_cost_basis)}</span></div>
                <div class="detail-row"><span class="label">Current Price</span><span class="value">${formatNum(h.current_price)}</span></div>
                <div class="detail-row"><span class="label">Allocation</span><span class="value">${h.weight_pct != null ? h.weight_pct.toFixed(1) + '%' : '\u2014'}</span></div>
                <div class="detail-row"><span class="label">Currency</span><span class="value" style="font-family:inherit">${esc(h.currency || 'USD')}</span></div>
            </div>

            <div class="detail-section">
                <h4>Risk Alerts${alertList.length ? ' (' + alertList.length + ')' : ''}</h4>
                ${alertList.length ? alertList.map(a => `
                    <div style="padding:0.3rem 0;border-bottom:1px solid var(--border);font-size:0.82rem;">
                        ${severityBadge(a.severity)} <span style="margin-left:0.3rem">${esc(a.title)}</span>
                    </div>`).join('') : '<span class="text-sm text-muted">No active alerts</span>'}
            </div>

            <div class="detail-section">
                <h4>Recent news${eventList.length ? ' (' + eventList.length + ')' : ''}</h4>
                ${eventList.length ? `<table class="detail-mini-table">
                    <tbody>${eventList.slice(0, 8).map(e => `<tr>
                        <td>${e.event_type ? `<span class="badge badge-muted">${esc(titleCase(e.event_type))}</span>` : ''}</td>
                        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(e.title)}">${esc(e.title || 'Untitled')}</td>
                        <td class="text-muted text-xs">${formatDateShort(e.published_at)}</td>
                    </tr>`).join('')}</tbody>
                </table>` : '<span class="text-sm text-muted">No news</span>'}
            </div>

            <div class="detail-section">
                <h4>Analysis Notes (${noteList.length})</h4>
                ${noteList.length ? noteList.slice(0, 5).map(n => {
                    let c = {};
                    try { c = JSON.parse(n.content || '{}'); } catch {}
                    const dir = c.impact_direction || 'neutral';
                    const dirCls = dir === 'positive' ? 'text-success' : dir === 'negative' ? 'text-danger' : 'text-muted';
                    return `<div style="padding:0.35rem 0;border-bottom:1px solid var(--border);font-size:0.82rem;">
                        <span class="${dirCls} font-medium">${esc(dir)}</span>
                        ${c.impact_magnitude ? `<span class="badge badge-muted" style="margin-left:0.3rem">${esc(c.impact_magnitude)}</span>` : ''}
                        <div class="text-xs text-muted mt-1" style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(c.short_term_outlook || '')}</div>
                    </div>`;
                }).join('') : '<span class="text-sm text-muted">No analysis notes</span>'}
            </div>

            <div class="mt-3" style="display:flex;gap:0.5rem;">
                <button class="btn btn-outline btn-sm" onclick="openEditHolding('${esc(h.id)}')">Edit</button>
                <button class="btn btn-outline btn-sm" onclick="openRecordTrade('${esc(h.ticker)}')">Record Trade</button>
            </div>`;
    };

    window.closeHoldingDetail = function () {
        const panel = $('#holding-detail');
        const overlay = $('#detail-overlay');
        if (panel) panel.classList.remove('open');
        if (overlay) overlay.classList.remove('open');
    };

    // ================================================================
    // Phase 9M — Live update dispatcher
    // ================================================================
    //
    // Single central routing function for every websocket payload
    // Axion receives.  The rules:
    //
    //   1. Parse defensively — malformed JSON, missing fields, and
    //      unknown message types are all no-ops (logged at debug
    //      level only, never user-visible).
    //   2. Honour a "modal open" guard — if any <dialog> is open OR
    //      any operator form has an active edit context, we DEFER
    //      refreshes for the affected surface and record a pending
    //      refresh that fires when the modal closes.
    //   3. Scope refreshes to the ACTIVE tab whenever possible — a
    //      user on the Holdings tab doesn't need the Events subtab
    //      re-fetching in the background.
    //   4. Portfolio-safety: refreshes check the incoming message's
    //      ``portfolio_id`` (when present) against the active one
    //      and skip cross-portfolio refreshes.
    //   5. Drive operator status refreshes via the Phase 9L poller
    //      — on an ``operator_action`` event, fire an immediate
    //      ``_opPollActionsStatus()`` without removing the 4-second
    //      polling fallback.
    //
    // Refresh rules (explicit):
    //
    //   alert{portfolio_id=ACTIVE}
    //     → if tab=alerts && no modal    → refreshTab('alerts')
    //     → if tab=portfolio && no modal → loadIntelligenceOverview()
    //     → if tab=portfolio && modal    → defer
    //
    //   event{linked_holding_count>0}
    //     → if tab=intelligence && subtab=events && no modal
    //         → refreshTab('events')
    //     → if tab=portfolio && no modal
    //         → loadIntelligenceOverview()
    //
    //   operator_action{action, state}
    //     → if tab=settings
    //         → _opPollActionsStatus()  (immediate, bypasses 4s wait)
    //     → if tab=settings && state==='finished' && no modal
    //         → also refresh operator factor + relationship tables
    //           (they may have drifted during the run)
    //
    //   holding_update
    //     → if tab=portfolio && subtab=holdings && no modal
    //         → refreshTab('holdings')
    //
    //   agent_complete
    //     → friendly toast
    //     → if tab=health, refreshTab('health')
    //
    //   ping / unknown → ignored silently
    //
    // Pending-refresh deferral: if the modal guard blocks a refresh,
    // the surface key is added to ``_wsPendingRefreshes``.  When the
    // next dialog closes, we flush the pending set.

    const _wsPendingRefreshes = new Set();
    let _wsConnected = false;

    function _wsAnyModalOpen() {
        // Any <dialog open> OR a detail panel in the slide-out state
        // counts as "user is mid-workflow — don't clobber them".
        const hasOpenDialog = document.querySelector('dialog[open]') != null;
        const slidePanel = document.querySelector('.detail-panel.open');
        return hasOpenDialog || slidePanel != null;
    }

    function _wsMessageIsForActivePortfolio(msg) {
        // Messages without a portfolio_id are treated as global and
        // always allowed (e.g. operator_action, agent_complete).
        if (!msg || msg.portfolio_id == null) return true;
        return String(msg.portfolio_id) === String(_activePortfolioId);
    }

    function _wsActiveTab() {
        return $('.tab-link.active')?.dataset.tab || null;
    }

    function _wsActiveSubtab(parent) {
        const parentPanel = document.getElementById('tab-' + parent);
        if (!parentPanel) return null;
        return parentPanel.querySelector('.sub-tab.active')?.dataset.subtab || null;
    }

    function _wsQueueOrRun(surfaceKey, fn) {
        // If a modal is open, defer.  Otherwise run immediately.
        if (_wsAnyModalOpen()) {
            _wsPendingRefreshes.add(surfaceKey);
            return false;
        }
        try { fn(); } catch (_e) { /* non-fatal */ }
        return true;
    }

    function _wsFlushPendingRefreshes() {
        if (_wsAnyModalOpen()) return;  // still blocked
        if (_wsPendingRefreshes.size === 0) return;
        const keys = Array.from(_wsPendingRefreshes);
        _wsPendingRefreshes.clear();
        for (const key of keys) {
            try {
                if (key === 'alerts' && _wsActiveTab() === 'alerts') {
                    refreshTab('alerts');
                } else if (key === 'events' && _wsActiveTab() === 'intelligence' && _wsActiveSubtab('intelligence') === 'events') {
                    refreshTab('events');
                } else if (key === 'intelligence-overview' && _wsActiveTab() === 'portfolio') {
                    if (typeof loadIntelligenceOverview === 'function') loadIntelligenceOverview();
                } else if (key === 'holdings' && _wsActiveTab() === 'portfolio' && _wsActiveSubtab('portfolio') === 'holdings') {
                    refreshTab('holdings');
                } else if (key === 'operator-tables' && _wsActiveTab() === 'settings') {
                    if (typeof loadOperatorFactorSensitivities === 'function') loadOperatorFactorSensitivities();
                    if (typeof loadOperatorRelationships === 'function') loadOperatorRelationships();
                }
            } catch (_e) { /* non-fatal */ }
        }
    }

    // Flush pending refreshes whenever a dialog closes, so deferred
    // updates land without requiring a manual refresh click.  We use
    // capture-phase so we catch the close event on any dialog.
    document.addEventListener('close', (ev) => {
        if (ev.target && ev.target.tagName === 'DIALOG') {
            // Let the dialog fully close before flushing
            setTimeout(_wsFlushPendingRefreshes, 50);
        }
    }, true);

    function _wsHandleAlert(msg) {
        if (!_wsMessageIsForActivePortfolio(msg)) return;
        const active = _wsActiveTab();
        if (active === 'alerts') {
            _wsQueueOrRun('alerts', () => refreshTab('alerts'));
        }
        if (active === 'portfolio') {
            _wsQueueOrRun('intelligence-overview', () => {
                if (typeof loadIntelligenceOverview === 'function') loadIntelligenceOverview();
            });
        }
        // Phase 9P — Inbox refresh.  If the Inbox sub-tab is visible
        // we re-render the full list; otherwise we only refresh the
        // unread badge so the sub-tab counter stays accurate.
        if (active === 'intelligence' && _wsActiveSubtab('intelligence') === 'inbox') {
            _wsQueueOrRun('inbox', () => {
                if (typeof loadInbox === 'function') loadInbox();
            });
        } else {
            if (typeof refreshInboxBadgeOnly === 'function') refreshInboxBadgeOnly();
        }
        // Desktop notification (opt-in, respects the user's settings)
        try {
            const s = getSettings();
            if (s.desktopNotif && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
                new Notification('Axion Alert', {
                    body: msg.title || 'New alert',
                    icon: '/dashboard/favicon.ico',
                });
            }
        } catch (_e) { /* non-fatal */ }
    }

    function _wsHandleEvent(msg) {
        // Only events that linked to at least one holding are
        // broadcast, so every event message is already relevance-
        // filtered.  Still scope by active tab.
        const active = _wsActiveTab();
        if (active === 'intelligence' && _wsActiveSubtab('intelligence') === 'events') {
            _wsQueueOrRun('events', () => refreshTab('events'));
        }
        if (active === 'portfolio') {
            _wsQueueOrRun('intelligence-overview', () => {
                if (typeof loadIntelligenceOverview === 'function') loadIntelligenceOverview();
            });
        }
        // Phase 9P — Inbox badge refresh for meaningful events.  The
        // inbox itself only surfaces events indirectly via alerts or
        // recommended actions, so we never need to do a full rebuild
        // on an event message; just keep the badge in sync.
        if (typeof refreshInboxBadgeOnly === 'function') refreshInboxBadgeOnly();
    }

    function _wsHandleOperatorAction(msg) {
        // Phase 9P — refresh the inbox when a relevant operator
        // action finishes, regardless of which tab is active.  This
        // runs BEFORE the settings-scoped early return so every
        // operator mutation updates the inbox unread badge.
        if (msg.state === 'finished') {
            const active = _wsActiveTab();
            if (active === 'intelligence' && _wsActiveSubtab('intelligence') === 'inbox') {
                _wsQueueOrRun('inbox', () => {
                    if (typeof loadInbox === 'function') loadInbox();
                });
            } else if (typeof refreshInboxBadgeOnly === 'function') {
                refreshInboxBadgeOnly();
            }
        }

        // The operator panel's Phase 9L status poller owns the lock
        // state.  Receiving an operator_action event is a signal to
        // refresh IMMEDIATELY instead of waiting for the next 4-second
        // interval.  Safe to call even if the poller isn't active —
        // _opPollActionsStatus() is idempotent.
        if (_wsActiveTab() !== 'settings') return;
        if (typeof _opPollActionsStatus === 'function') {
            _opPollActionsStatus();
        }
        // When the action finishes, the operator tables may have
        // drifted (e.g. reconcile added/removed seed rows).  Only
        // refresh them if no modal is open — we never want to
        // clobber a mid-edit override form.
        if (msg.state === 'finished') {
            _wsQueueOrRun('operator-tables', () => {
                if (typeof loadOperatorFactorSensitivities === 'function') loadOperatorFactorSensitivities();
                if (typeof loadOperatorRelationships === 'function') loadOperatorRelationships();
            });
        }
    }

    function _wsHandleAgentComplete(msg) {
        try {
            showToast(`${msg.agent || 'Agent'} completed`);
        } catch (_e) { /* non-fatal */ }
        if (_wsActiveTab() === 'settings') {
            // The health card under Settings carries the agent status
            if (typeof loadHealth === 'function') loadHealth();
        }
    }

    function _wsHandleHoldingUpdate(_msg) {
        if (_wsActiveTab() !== 'portfolio') return;
        const sub = _wsActiveSubtab('portfolio');
        if (sub === 'holdings') {
            _wsQueueOrRun('holdings', () => refreshTab('holdings'));
        } else if (sub === 'exposures') {
            _wsQueueOrRun('exposures', () => refreshTab('exposures'));
        }
    }

    // The single entry point for every websocket payload.  Exposed
    // on the window object so E2E tests + the reconnect loop can
    // reach it without grepping the closure.
    function _wsDispatch(msg) {
        if (!msg || typeof msg !== 'object') return;
        const t = msg.type;
        if (typeof t !== 'string') return;
        switch (t) {
            case 'ping':                       return;
            case 'alert':                      return _wsHandleAlert(msg);
            case 'event':                      return _wsHandleEvent(msg);
            case 'operator_action':            return _wsHandleOperatorAction(msg);
            case 'agent_complete':             return _wsHandleAgentComplete(msg);
            case 'holding_update':             return _wsHandleHoldingUpdate(msg);
            default:
                // Unknown message type — log at debug for developer
                // visibility, but never bubble to the user.
                if (window.console && console.debug) {
                    console.debug('[ws] ignoring unknown message type:', t);
                }
                return;
        }
    }
    window._wsDispatch = _wsDispatch;

    // ================================================================
    // WebSocket Real-Time Updates (transport + reconnect loop)
    // ================================================================
    function connectWebSocket() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${proto}//${location.host}/api/v1/ws`;
        let ws;
        try {
            ws = new WebSocket(wsUrl);
        } catch { return; }

        ws.onopen = () => { _wsConnected = true; };

        ws.onmessage = (event) => {
            let msg;
            try {
                msg = JSON.parse(event.data);
            } catch (_e) {
                return;  // malformed — silently ignore
            }
            _wsDispatch(msg);
        };

        ws.onclose = () => {
            _wsConnected = false;
            setTimeout(connectWebSocket, 5000);
        };

        ws.onerror = () => {
            _wsConnected = false;
            ws.close();
        };
    }

    // ================================================================
    // Keyboard Shortcuts
    // ================================================================
    document.addEventListener('keydown', function (e) {
        // Escape closes any open modal
        if (e.key === 'Escape') {
            $$('dialog[open]').forEach(d => d.close());
        }
        // Ctrl+N / Cmd+N = Add Holding (when not in input)
        if ((e.ctrlKey || e.metaKey) && e.key === 'n' && !e.target.closest('input, textarea, select, dialog')) {
            e.preventDefault();
            window.openAddHolding();
        }
    });

    // ================================================================
    // Initialization
    // ================================================================
    document.addEventListener('DOMContentLoaded', function () {
        // Check nav overflow — toggle fade mask
        function checkNavOverflow() {
            const tabs = document.querySelector('.nav-tabs');
            if (tabs) tabs.classList.toggle('no-overflow', tabs.scrollWidth <= tabs.clientWidth + 2);
        }
        checkNavOverflow();
        window.addEventListener('resize', checkNavOverflow);

        // Populate currency dropdowns
        populateCurrencySelects();

        // Tab navigation
        $$('.tab-link').forEach(link => {
            link.addEventListener('click', () => switchTab(link.dataset.tab));
        });

        // Sub-tab navigation
        $$('.sub-tab').forEach(btn => {
            btn.addEventListener('click', () => loadSubTab(btn.dataset.parent, btn.dataset.subtab));
        });

        // AI provider selection — update UI when dropdowns change
        const primaryProvSel = document.getElementById('setting-primary-provider');
        const backupProvSel = document.getElementById('setting-backup-provider');
        if (primaryProvSel) primaryProvSel.addEventListener('change', updateProviderUI);
        if (backupProvSel) backupProvSel.addEventListener('change', updateProviderUI);

        // Holdings search
        const search = $('#holdings-search');
        if (search) {
            search.addEventListener('input', () => filterHoldings(search.value));
        }

        // Audit filter
        const auditFilter = $('#audit-filter');
        if (auditFilter) {
            auditFilter.addEventListener('change', () => {
                tabLoaded.audit = true;
                loadAudit(auditFilter.value);
            });
        }

        // Phase 9P — inbox refresh + mark-all controls
        const inboxRefreshBtn = $('#inbox-refresh-btn');
        if (inboxRefreshBtn) {
            inboxRefreshBtn.addEventListener('click', () => loadInbox());
        }
        const inboxMarkAllBtn = $('#inbox-mark-all-read-btn');
        if (inboxMarkAllBtn) {
            inboxMarkAllBtn.addEventListener('click', () => markAllInboxRead());
        }
        // Refresh the unread badge once on startup so it's accurate
        // before the operator even opens the Inbox sub-tab.
        setTimeout(() => {
            if (typeof refreshInboxBadgeOnly === 'function') refreshInboxBadgeOnly();
        }, 600);

        // Phase 9V/9W — alerts severity + acknowledged filters
        const sevFilter = $('#alerts-severity-filter');
        if (sevFilter) {
            sevFilter.addEventListener('change', () => {
                tabLoaded.alerts = true;
                loadAlerts();
            });
        }
        const ackFilter = $('#alerts-ack-filter');
        if (ackFilter) {
            ackFilter.addEventListener('change', () => {
                tabLoaded.alerts = true;
                loadAlerts();
            });
        }

        // Phase 9U — saved views controls
        const saveViewBtn = $('#save-current-view-btn');
        if (saveViewBtn) saveViewBtn.addEventListener('click', () => _saveCurrentView());
        const refreshViewsBtn = $('#saved-views-refresh');
        if (refreshViewsBtn) refreshViewsBtn.addEventListener('click', () => loadSavedViews());

        // Events search
        const eventsSearch = $('#events-search');
        if (eventsSearch) {
            eventsSearch.addEventListener('input', () => filterEvents(eventsSearch.value));
        }

        // Analysis filter
        const analysisFilter = $('#analysis-filter');
        if (analysisFilter) {
            analysisFilter.addEventListener('change', () => {
                tabLoaded.analysis = true;
                loadAnalysisNotes(analysisFilter.value);
            });
        }

        // Live previews for forms
        ['ah-quantity', 'ah-price', 'ah-cost', 'ah-currency'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', updateAddPreview);
        });
        ['tr-quantity', 'tr-price', 'tr-type', 'tr-currency'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', updateTradePreview);
            if (el) el.addEventListener('change', updateTradePreview);
        });

        // Table sort delegation
        document.addEventListener('click', (e) => {
            const th = e.target.closest('th.sortable');
            if (!th) return;
            const col = th.dataset.sort;
            if (!col) return;
            // Check sub-panel first, then parent tab-panel
            const subPanel = th.closest('.sub-panel');
            const panelId = subPanel ? subPanel.id : (th.closest('.tab-panel')?.id || '');
            if (panelId === 'subtab-events') {
                sortTable('events-table', allEvents, renderEventsTable, col);
            } else if (panelId === 'subtab-holdings') {
                sortTable('holdings-table', allHoldings, renderHoldingsTable, col);
            }
        });

        // Phase 9B — open event detail modal when clicking an event row
        document.addEventListener('click', (e) => {
            const row = e.target.closest('.events-row-clickable');
            if (!row) return;
            // Ignore clicks inside anchors/buttons — those have their own semantics.
            if (e.target.closest('a,button')) return;
            const eid = row.dataset.eventId;
            if (eid) window.openEventDetail(eid);
        });

        // Alert acknowledge via event delegation
        const alertsContent = $('#alerts-content');
        if (alertsContent) {
            alertsContent.addEventListener('click', (e) => {
                const btn = e.target.closest('.btn-ack');
                if (btn && btn.dataset.alertId) {
                    window.acknowledgeAlert(btn.dataset.alertId);
                }
            });
        }

        // Modal close buttons via event delegation
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-close-modal]');
            if (btn) {
                const dialog = btn.closest('dialog');
                if (dialog) dialog.close();
            }
        });

        // Drop zone drag & drop
        const dz = $('#drop-zone');
        if (dz) {
            dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag-over'); });
            dz.addEventListener('dragleave', () => dz.classList.remove('drag-over'));
            dz.addEventListener('drop', e => {
                e.preventDefault();
                dz.classList.remove('drag-over');
                const fi = $('#portfolio-file');
                fi.files = e.dataTransfer.files;
                window.onFileSelected(fi);
            });
        }

        // Apply saved settings
        const savedSettings = getSettings();
        const defaultTab = savedSettings.defaultTab || 'portfolio';

        // Load portfolio selector then default tab.
        // Phase 9R — after the default tab loads, consume any URL
        // hash deep-link so a shared/bookmarked link restores the
        // exact navigation state from a cold page load.
        _loadPortfolioSelector().then(() => {
            switchTab(defaultTab);
            _consumeInitialNavHash();
        });

        // Apply refresh interval from settings
        applySettings(savedSettings);

        // Tab badge counts (update every 60s alongside sidebar)
        async function updateTabBadges() {
            try {
                const [alerts, events] = await Promise.all([
                    fetchJSON(_pq(API.alertsActive) + '&limit=200').catch(() => []),
                    fetchJSON(API.events + '?limit=500').catch(() => []),
                ]);
                const alertList = ensureArray(alerts, 'items', 'alerts');
                const eventList = ensureArray(events, 'items', 'events');
                const ab = document.getElementById('badge-alerts');
                const eb = document.getElementById('badge-events');
                if (ab) { if (alertList.length > 0) { ab.textContent = alertList.length > 99 ? '99+' : alertList.length; ab.hidden = false; ab.className = 'tab-badge badge-warning'; } else { ab.hidden = true; } }
                if (eb) { if (eventList.length > 0) { eb.textContent = eventList.length > 99 ? '99+' : eventList.length; eb.hidden = false; } else { eb.hidden = true; } }
            } catch {}
        }
        updateTabBadges();
        setInterval(updateTabBadges, 60000);

        // Overview band auto-refresh (health chips update every 60s)
        setInterval(async () => {
            try {
                const h = await fetchJSON(API.health).catch(() => null);
                if (!h) return;
                const chips = document.querySelectorAll('.overview-chip');
                if (chips.length >= 3) {
                    const srcChip = Array.from(chips).find(c => c.textContent.includes('sources'));
                    const collChip = Array.from(chips).find(c => c.textContent.includes('Collected'));
                    if (srcChip) srcChip.textContent = `${h.sources_active ?? '?'} sources`;
                    if (collChip) collChip.textContent = `Collected ${h.last_collection ? timeAgo(h.last_collection) : 'never'}`;
                }
                window._lastHealthData = h;
            } catch {}
        }, 60000);

        // Connect WebSocket for real-time updates
        connectWebSocket();
    });

    // Cleanup on page unload
    window.addEventListener('beforeunload', () => {
        // (sidebar cleanup removed)
    });
})();
