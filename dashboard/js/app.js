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
        analysisNotes:'/api/v1/analysis/notes',
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

    async function fetchJSON(url) {
        const res = await fetch(url);
        if (!res.ok) {
            if (res.status === 404) return null;
            throw new Error(`HTTP ${res.status}`);
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
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
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
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        return res.json();
    }

    async function deleteJSON(url) {
        const res = await fetch(url, { method: 'DELETE' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
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
    // Holdings Tab
    // ================================================================
    let allHoldings = [];

    async function loadHoldings() {
        const summaryEl = $('#holdings-summary');
        const tableEl = $('#holdings-table');
        if (!tableEl) { console.error('holdings-table element not found'); return; }
        // Show skeleton while loading (only on first load)
        if (!allHoldings.length && tableEl.querySelector('.spinner')) {
            tableEl.innerHTML = renderSkeleton(6);
        }
        try {
            const [summary, holdings, health] = await Promise.all([
                fetchJSON(API.summary).catch(() => null),
                fetchJSON(API.holdings).catch(() => []),
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

                    summaryEl.innerHTML = `<div class="overview-band">
                        <div class="overview-stat">
                            <div class="label">Portfolio Value</div>
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
        const coreFeatures = 'Portfolio tracking, alerts, news collection, CSV and structured PDF import, holdings management, trade recording, and exposure analysis.';
        const aiFeatures = 'Event classification, portfolio analysis notes, intelligence digests, natural language chat, and image/scanned PDF extraction.';

        return `<div class="welcome-card">
            <div class="welcome-header">
                <div>
                    <div class="welcome-title">Welcome to Axion</div>
                    <div class="welcome-subtitle">Portfolio intelligence by 4Labs. Get started in a few steps.</div>
                </div>
            </div>
            <div class="welcome-steps">
                <div class="welcome-step ${sysOk ? 'done' : ''}">
                    <span class="step-icon">${sysOk ? check : circle}</span>
                    <div>
                        <div class="step-label">System running</div>
                        <div class="step-hint">${sysOk ? `${srcCount} news sources active` : 'Starting up\u2026'}</div>
                    </div>
                </div>
                <div class="welcome-step">
                    <span class="step-icon">${circle}</span>
                    <div>
                        <div class="step-label">Upload your portfolio</div>
                        <div class="step-hint">CSV with ticker, quantity, price, currency columns</div>
                    </div>
                </div>
                <div class="welcome-step ${llmOk ? 'done' : ''}">
                    <span class="step-icon">${llmOk ? check : circle}</span>
                    <div>
                        <div class="step-label">Configure AI provider <span class="text-xs text-muted">(optional)</span></div>
                        <div class="step-hint">${llmOk ? 'AI-enhanced analysis active' : 'Adds event classification, analysis notes, digests, and chat. Core features work without AI.'}</div>
                    </div>
                </div>
                <div class="welcome-step ${collected ? 'done' : ''}">
                    <span class="step-icon">${collected ? check : circle}</span>
                    <div>
                        <div class="step-label">First news collection</div>
                        <div class="step-hint">${collected ? 'Events collected' : 'Happens automatically every 30 minutes after portfolio upload'}</div>
                    </div>
                </div>
            </div>
            <div class="welcome-capabilities">
                <div class="capabilities-summary">
                    <div class="capabilities-group">
                        <div class="capabilities-label"><span class="dot green"></span>Available now</div>
                        <div class="capabilities-text">${coreFeatures}</div>
                    </div>
                    ${!llmOk ? `<div class="capabilities-group">
                        <div class="capabilities-label"><span class="dot yellow"></span>With AI provider</div>
                        <div class="capabilities-text">${aiFeatures}</div>
                    </div>` : `<div class="capabilities-group">
                        <div class="capabilities-label"><span class="dot green"></span>AI features</div>
                        <div class="capabilities-text">${aiFeatures}</div>
                    </div>`}
                </div>
            </div>
            <div class="welcome-actions">
                <button class="btn btn-primary" onclick="uploadPortfolio()">Upload Portfolio</button>
                <button class="btn btn-outline" onclick="document.querySelector('[data-tab=settings]').click()">Open Settings</button>
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
                <th class="num sortable" data-sort="_mv">Mkt Value</th><th class="num sortable" data-sort="_wt">Weight</th><th class="num sortable" data-sort="_pnl">P&L</th><th class="num sortable" data-sort="_pnl_pct">P&L %</th>
                <th style="width:70px;"></th>
            </tr></thead>
            <tbody>${enriched.map(h => `<tr>
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
                showToast('Failed: ' + e.message, 'error');
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
                showToast('Failed: ' + e.message, 'error');
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
                showToast('Failed: ' + e.message, 'error');
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
                showToast('Failed: ' + e.message, 'error');
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
            const results = await Promise.all(dims.map(d => fetchJSON(API.exposure(d)).catch(() => null)));
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
            const data = await fetchJSON(API.trades);
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

    function renderEventsTable(list) {
        const el = $('#events-table');
        if (!list.length) {
            el.innerHTML = renderEmpty('events', 'No events collected yet.', {
                hint: 'Events appear here as sources collect news.',
                actions: [{ label: 'Run Collection', onclick: "runAction('collection')", primary: true }]
            });
            return;
        }
        el.innerHTML = `<div class="table-wrap"><table>
            <thead><tr>
                <th class="sortable" data-sort="title">Title</th>
                <th class="sortable" data-sort="event_type">Type</th>
                <th class="sortable" data-sort="materiality">Materiality</th>
                <th>Source</th>
                <th class="sortable" data-sort="published_at">Published</th>
            </tr></thead>
            <tbody>${list.map(e => `<tr>
                <td><a href="${esc(e.url || '#')}" target="_blank" rel="noopener">${esc(e.title || 'Untitled')}</a></td>
                <td>${e.event_type ? `<span class="badge badge-muted">${esc(titleCase(e.event_type))}</span>` : '<span class="text-muted">\u2014</span>'}</td>
                <td>${e.materiality && e.materiality !== 'unscored' ? `<span class="badge badge-${e.materiality === 'critical' ? 'critical' : e.materiality === 'high' ? 'high' : 'info'}">${esc(e.materiality)}</span>` : '<span class="text-muted">unscored</span>'}</td>
                <td class="text-sm text-muted">${esc(e.source_id || '\u2014')}</td>
                <td class="text-sm text-muted">${formatDate(e.published_at)}</td>
            </tr>`).join('')}</tbody></table></div>`;
    }

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
            let url = API.analysisNotes;
            if (ticker) url += `?ticker=${ticker}`;
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
                    hint: 'Analysis is generated automatically from collected events.',
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

    async function loadDigest() {
        const el = $('#digest-content');
        try {
            const data = await fetchJSON(API.digestLatest);
            if (!data) {
                el.innerHTML = renderEmpty('digest', 'No digests generated yet.', {
                    hint: 'Digests summarize your portfolio activity.',
                    actions: [{ label: 'Generate Digest', onclick: 'generateDigest()', primary: true }]
                });
                return;
            }
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
    async function loadAlerts() {
        const el = $('#alerts-content');
        try {
            const data = await fetchJSON(API.alertsActive);
            const list = ensureArray(data, 'items', 'alerts');
            if (!list.length) {
                el.innerHTML = renderEmpty('check', 'No active alerts.', {
                    hint: 'Alerts appear when risks are detected in your portfolio.'
                });
                return;
            }
            el.innerHTML = list.map(a => `
                <div class="alert-card severity-${(a.severity || 'info').toLowerCase()}">
                    <div class="flex justify-between items-center gap-2">
                        <div class="alert-title">${severityBadge(a.severity)} ${esc(titleCase(a.title))}</div>
                        ${!a.acknowledged ? `<button class="btn btn-sm btn-ghost btn-ack" data-alert-id="${esc(a.id)}">Acknowledge</button>` : ''}
                    </div>
                    <div class="text-sm text-muted mt-2">${esc(a.message?.replace(/_/g, ' '))}</div>
                    <div class="alert-meta">
                        <span class="text-xs text-muted">${timeAgo(a.created_at)}</span>
                        ${(a.related_holdings || []).length ? `<div class="alert-tickers">${a.related_holdings.map(hid => {
                            const h = allHoldings.find(x => x.id === hid);
                            return h ? `<span class="ticker-badge">${esc(h.ticker)}</span>` : '';
                        }).filter(Boolean).join('')}</div>` : ''}
                    </div>
                </div>`).join('');
        } catch (e) {
            el.innerHTML = renderError('alerts: ' + e.message);
        }
    }

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
                        <div class="health-item"><div class="label">Analysis Mode</div><div class="value">${health.llm_available ? `${statusDot('ok')} AI-enhanced` : `${statusDot('idle')} Core mode`}</div></div>
                        ${!health.llm_available ? `<div class="health-item" style="grid-column:1/-1;"><div class="label">Core Mode</div><div class="value text-xs text-muted" style="font-family:var(--font-sans);">Portfolio tracking, alerts, news collection, and CSV/PDF import are fully active. Add an AI provider key above to enable event classification, analysis notes, digests, and chat.</div></div>` : ''}
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
                showToast('Extraction failed: ' + e.message, 'error');
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
                    body: JSON.stringify({ rows: rows }),
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
            showToast('Failed: ' + e.message, 'error');
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
                const mode = h.llm_available ? 'AI-enhanced' : 'Core mode';
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
            const res = await fetch('/api/v1/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query }),
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
            const modeLabel = data.mode === 'rule-based' ? 'Core mode' : data.mode;
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
                hint.textContent = 'Select a provider above and add an API key to enable AI-enhanced analysis.';
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
            showToast('Failed: ' + e.message, 'error');
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
                showToast('Failed: ' + e.message, 'error');
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
                showToast('Failed: ' + e.message, 'error');
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
                showToast('Failed: ' + e.message, 'error');
            }
        });
    };

    // Patch the tab loader so Settings loads API key status + health + sources
    tabLoaders.settings = function () {
        loadSettings();
        loadApiKeyStatus();
        loadHealth();
        loadSources();
    };

    // Update the placeholder based on selected provider
    function updateKeyPlaceholder(role) {
        const providerSelect = document.getElementById('setting-' + role + '-provider');
        const keyInput = document.getElementById('setting-' + role + '-key');
        if (!providerSelect || !keyInput) return;
        keyInput.placeholder = _KEY_PLACEHOLDERS[providerSelect.value] || 'API key...';
    }

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
            fetchJSON(`${API.analysisNotes}?ticker=${h.ticker}`).catch(() => []),
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
                <div class="detail-row"><span class="label">Weight</span><span class="value">${h.weight_pct != null ? h.weight_pct.toFixed(1) + '%' : '\u2014'}</span></div>
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
                <h4>Recent Events${eventList.length ? ' (' + eventList.length + ')' : ''}</h4>
                ${eventList.length ? `<table class="detail-mini-table">
                    <tbody>${eventList.slice(0, 8).map(e => `<tr>
                        <td>${e.event_type ? `<span class="badge badge-muted">${esc(titleCase(e.event_type))}</span>` : ''}</td>
                        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(e.title)}">${esc(e.title || 'Untitled')}</td>
                        <td class="text-muted text-xs">${formatDateShort(e.published_at)}</td>
                    </tr>`).join('')}</tbody>
                </table>` : '<span class="text-sm text-muted">No events</span>'}
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
    // WebSocket Real-Time Updates
    // ================================================================
    function connectWebSocket() {
        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${proto}//${location.host}/api/v1/ws`;
        let ws;
        try {
            ws = new WebSocket(wsUrl);
        } catch { return; }

        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                if (msg.type === 'alert') {
                    // (sidebar removed)
                    if ($('.tab-link.active')?.dataset.tab === 'alerts') refreshTab('alerts');
                    const s = getSettings();
                    if (s.desktopNotif && Notification.permission === 'granted') {
                        new Notification('Axion Alert', { body: msg.title || 'New alert', icon: '/dashboard/favicon.ico' });
                    }
                } else if (msg.type === 'agent_complete') {
                    showToast(`${msg.agent || 'Agent'} completed`);
                    const active = $('.tab-link.active')?.dataset.tab;
                    if (active === 'health') refreshTab('health');
                } else if (msg.type === 'event') {
                    if ($('.tab-link.active')?.dataset.tab === 'events') refreshTab('events');
                } else if (msg.type === 'holding_update') {
                    refreshTab('holdings');
                    refreshTab('exposures');
                }
            } catch {}
        };

        ws.onclose = () => {
            setTimeout(connectWebSocket, 5000);
        };

        ws.onerror = () => {
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

        // Load default tab
        switchTab(defaultTab);

        // Apply refresh interval from settings
        applySettings(savedSettings);

        // Tab badge counts (update every 60s alongside sidebar)
        async function updateTabBadges() {
            try {
                const [alerts, events] = await Promise.all([
                    fetchJSON(API.alertsActive + '?limit=200').catch(() => []),
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
