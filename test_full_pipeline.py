#!/usr/bin/env python3
"""Axion -- Comprehensive Pipeline Test Suite.

Feeds fake portfolio + fake news into the live system and verifies
every agent, API endpoint, and edge case works as intended.

Usage:
    python test_full_pipeline.py [--phase N] [--base-url URL]

Requires: requests (pip install requests)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package is required.  pip install requests")
    sys.exit(1)

# Force UTF-8 on Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_URL = "http://localhost:7777"
API_PREFIX = "/api/v1"
API_KEY = os.environ.get("KLEITOS_API_KEY", "")
CONTAINER_NAME = "kleitos-app"  # Docker container name
AGENT_WAIT_SECONDS = 60
AGENT_POLL_INTERVAL = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_pass = _fail = _skip = 0
_results: list[tuple[str, str, str]] = []


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def api(method: str, path: str, **kwargs: Any) -> requests.Response:
    url = f"{BASE_URL}{API_PREFIX}{path}"
    return requests.request(method, url, headers=_headers(), timeout=30, **kwargs)


def api_get(path: str, **params: Any) -> requests.Response:
    return api("GET", path, params=params)


def api_post(path: str, json_body: dict | None = None, **kwargs: Any) -> requests.Response:
    return api("POST", path, json=json_body, **kwargs)


def api_put(path: str, json_body: dict) -> requests.Response:
    return api("PUT", path, json=json_body)


def api_delete(path: str) -> requests.Response:
    return api("DELETE", path)


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _pass, _fail
    status = "PASS" if condition else "FAIL"
    if condition:
        _pass += 1
    else:
        _fail += 1
    tag = f"  {status} #{_pass + _fail}"
    msg = f"{tag}: {name}"
    if detail and not condition:
        msg += f"  -- {detail}"
    print(msg)
    _results.append((status, name, detail))
    return condition


def skip(name: str, reason: str = ""):
    global _skip
    _skip += 1
    print(f"  SKIP: {name}  -- {reason}")
    _results.append(("SKIP", name, reason))


def section(title: str):
    print(f"\n--- {title} ---")


def wait_for_agent(agent_id: str, timeout: int = AGENT_WAIT_SECONDS) -> dict | None:
    """Poll agent runs until the latest one completes or times out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api_get("/agents/runs", agent_id=agent_id, limit=1)
        if r.status_code == 200:
            data = r.json()
            runs = data if isinstance(data, list) else data.get("items", data.get("runs", []))
            if runs and isinstance(runs, list) and len(runs) > 0:
                latest = runs[0]
                status = latest.get("status", "")
                if status in ("completed", "failed", "error"):
                    return latest
        time.sleep(AGENT_POLL_INTERVAL)
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def docker_exec_python(script: str) -> str:
    """Run a Python script inside the Docker container and return stdout."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "axion", "python", "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  DOCKER EXEC ERROR: {result.stderr[:300]}")
    return result.stdout.strip()


def get_json(body: Any) -> list:
    """Extract list of items from API response body (handles various shapes)."""
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("items", "holdings", "events", "notes", "alerts",
                     "trades", "runs", "entries", "digests"):
            if key in body:
                return body[key]
    return []


# ---------------------------------------------------------------------------
# Test Portfolio CSV
# ---------------------------------------------------------------------------
TEST_PORTFOLIO_CSV = """\
ticker,quantity,price,cost,currency,venue,isin
AAPL,150,178.50,145.20,USD,NASDAQ,US0378331005
MSFT,120,380.00,310.50,USD,NASDAQ,US5949181045
NVDA,80,475.00,220.00,USD,NASDAQ,US67066G1040
JPM,200,170.50,145.00,USD,NYSE,US46625H1005
JNJ,180,155.00,160.00,USD,NYSE,US4781601046
ASML,30,680.00,550.00,EUR,EURONEXT AMSTERDAM,NL0010273215
TSLA,60,245.00,180.00,USD,NASDAQ,US88160R1014
XOM,250,105.00,88.00,USD,NYSE,US30231G1022
NVO,100,120.00,95.00,DKK,NYSE,US6701002056
PG,140,155.00,140.00,USD,NYSE,US7427181091
AMT,90,210.00,195.00,USD,NYSE,US03027X1000
NEE,160,75.00,65.00,USD,NYSE,US65339F1012
BABA,200,85.00,110.00,USD,NYSE,US01609W1027
HD,70,340.00,310.00,USD,NYSE,US4370761029
BA,100,220.00,190.00,USD,NYSE,US0970231058
"""


# ---------------------------------------------------------------------------
# Test Events
# ---------------------------------------------------------------------------
TEST_EVENTS = [
    {
        "title": "Apple Reports Record Q1 Revenue, Beats Estimates by 12%",
        "summary": "Apple Inc beat Wall Street expectations with record Q1 revenue.",
        "event_type": "earnings",
        "tickers": ["AAPL"],
    },
    {
        "title": "NVIDIA Misses Revenue Estimates, Guidance Below Expectations",
        "summary": "NVIDIA reported Q4 revenue below consensus estimate.",
        "event_type": "earnings",
        "tickers": ["NVDA"],
    },
    {
        "title": "Microsoft Reports In-Line Q3 Results, Cloud Growth Steady",
        "summary": "Microsoft posted Q3 results broadly in line with expectations.",
        "event_type": "earnings",
        "tickers": ["MSFT"],
    },
    {
        "title": "EU Passes Sweeping AI Regulation Act Affecting All Major Tech Companies",
        "summary": "The EU AI Act imposes new compliance requirements on AI products.",
        "event_type": "regulatory",
        "tickers": ["AAPL", "MSFT", "NVDA"],
    },
    {
        "title": "Oil Prices Surge 15% on OPEC+ Production Cuts, Highest Since 2022",
        "summary": "Brent crude surged to $95/barrel after OPEC+ surprise cuts.",
        "event_type": "macro",
        "tickers": ["XOM"],
    },
    {
        "title": "JPMorgan Announces Quarterly Dividend of $1.05 Per Share",
        "summary": "JPMorgan declared a quarterly dividend payable next month.",
        "event_type": "dividend",
        "tickers": ["JPM"],
    },
    {
        "title": "FDA Approves New Drug Class Benefiting Major Pharma Companies",
        "summary": "FDA approved a new class of GLP-1 drugs for pharma.",
        "event_type": "regulatory",
        "tickers": ["JNJ", "NVO"],
    },
    {
        "title": "China Regulators Launch Investigation Into Major E-Commerce Platforms",
        "summary": "Chinese authorities probe e-commerce monopolistic practices.",
        "event_type": "regulatory",
        "tickers": ["BABA"],
    },
    {
        "title": "Federal Reserve Signals Additional Rate Hikes, REIT Sector Under Pressure",
        "summary": "The Fed signaled more rate hikes, pressuring REITs and utilities.",
        "event_type": "macro",
        "tickers": ["AMT", "NEE"],
    },
    {
        "title": "Boeing Faces New Safety Investigation After Multiple Incidents Reported",
        "summary": "FAA launched a safety investigation into Boeing 737 MAX fleet.",
        "event_type": "litigation",
        "tickers": ["BA"],
    },
    {
        "title": "Procter & Gamble Posts Modest Growth, Organic Sales Up 2%",
        "summary": "P&G reported modest organic sales growth of 2%.",
        "event_type": "earnings",
        "tickers": ["PG"],
    },
    {
        "title": "Tesla Recalls 500,000 Vehicles Over Autopilot Software Issues",
        "summary": "NHTSA investigation led to recall due to Autopilot safety concerns.",
        "event_type": "regulatory",
        "tickers": ["TSLA"],
    },
    {
        "title": "Consumer Spending Slows as Inflation Erodes Purchasing Power",
        "summary": "US consumer spending grew only 0.1% last month.",
        "event_type": "macro",
        "tickers": ["HD", "BABA"],
    },
    # Historical negatives for TSLA thesis-drift detection
    {
        "title": "Tesla Sales Decline 20% in Key European Markets",
        "summary": "Tesla deliveries fell sharply across Europe.",
        "event_type": "news",
        "tickers": ["TSLA"],
        "days_ago": 10,
    },
    {
        "title": "Tesla Faces Class Action Lawsuit Over Autopilot Claims",
        "summary": "A class-action lawsuit alleges Tesla misled consumers.",
        "event_type": "litigation",
        "tickers": ["TSLA"],
        "days_ago": 20,
    },
]


# ---------------------------------------------------------------------------
# Phase 0: Health Check + Database Reset
# ---------------------------------------------------------------------------
def phase_0():
    section("Phase 0 -- Health Check")
    try:
        r = requests.get(f"{BASE_URL}{API_PREFIX}/health", timeout=5)
        ok = r.status_code == 200
        check("Health endpoint returns 200", ok, f"got {r.status_code}")
        if ok:
            body = r.json()
            check("Database connected", body.get("database") == "connected",
                  f"got database={body.get('database')}")
        return ok
    except Exception as exc:
        check("Server reachable", False, str(exc))
        return False


def reset_database():
    """Wipe all data tables for a clean test run."""
    section("Phase 0.5 -- Database Reset")
    script = """
import sqlite3
conn = sqlite3.connect('/data/db/kleitos.db')
tables = ['analysis_notes', 'event_links', 'events', 'alerts', 'coverage_reports',
          'digests', 'trades', 'holdings', 'securities', 'sources', 'agent_runs', 'audit_log']
deleted = 0
for t in tables:
    try:
        n = conn.execute(f'DELETE FROM {t}').rowcount
        deleted += n
    except Exception:
        pass
conn.commit()
conn.close()
print(f'RESET_OK:{deleted}')
"""
    out = docker_exec_python(script)
    ok = "RESET_OK" in out
    check("Database reset successful", ok, out[:200])
    return ok


# ---------------------------------------------------------------------------
# Phase 1: Portfolio Setup
# ---------------------------------------------------------------------------
def phase_1():
    section("Phase 1.1 -- CSV Upload")

    files = {"file": ("portfolio.csv", TEST_PORTFOLIO_CSV, "text/csv")}
    r = api("POST", "/portfolio/upload", files=files)
    check("CSV upload returns 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        body = r.json()
        imported = body.get("holdings_imported", body.get("imported", 0))
        check(f"Imported 15 holdings (got {imported})", imported == 15)
        errs = body.get("errors", [])
        check("No upload errors", len(errs) == 0, f"errors: {errs[:3]}")

    section("Phase 1.2 -- Holdings Verification")
    r = api_get("/portfolio/holdings", limit=50)
    check("List holdings returns 200", r.status_code == 200)
    if r.status_code == 200:
        items = get_json(r.json())
        check(f"15 holdings returned (got {len(items)})", len(items) == 15)

        tickers = {h.get("ticker") for h in items}
        for t in ["AAPL", "MSFT", "NVDA", "JPM", "ASML", "TSLA", "XOM", "BABA", "BA"]:
            check(f"Ticker {t} present", t in tickers)

        # Store holding IDs
        global HOLDING_MAP
        HOLDING_MAP = {h["ticker"]: h["id"] for h in items}

        # Note: market_value is calculated after trades/weight updates,
        # so it may be 0 right after initial upload. We verify it later.

    section("Phase 1.3 -- Portfolio Summary")
    r = api_get("/portfolio/summary")
    check("Summary returns 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        count = body.get("holding_count", body.get("total_holdings", 0))
        check(f"Summary shows 15 holdings (got {count})", count == 15)
        mv = body.get("total_market_value", 0)
        check("Total market value > 0", mv > 0, f"got {mv}")

    section("Phase 1.4 -- Exposure Dimensions")
    for dim in ["sector", "geography", "currency"]:
        r = api_get("/portfolio/exposure", dimension=dim)
        check(f"Exposure by {dim} returns 200", r.status_code == 200, f"got {r.status_code}")

    section("Phase 1.5 -- Trade Lifecycle")

    # Buy 50 more AAPL
    trade = {
        "ticker": "AAPL", "trade_type": "buy", "quantity": 50,
        "price": 180.00, "trade_date": now_iso()[:10], "currency": "USD",
    }
    r = api_post("/portfolio/trades", trade)
    buy_ok = r.status_code in (200, 201)
    check("Buy 50 AAPL accepted", buy_ok, f"got {r.status_code}: {r.text[:200]}")

    # Sell 30 MSFT
    trade2 = {
        "ticker": "MSFT", "trade_type": "sell", "quantity": 30,
        "price": 390.00, "trade_date": now_iso()[:10], "currency": "USD",
    }
    r = api_post("/portfolio/trades", trade2)
    check("Sell 30 MSFT accepted", r.status_code in (200, 201), f"got {r.status_code}: {r.text[:200]}")

    # Dividend on JPM
    trade3 = {
        "ticker": "JPM", "trade_type": "dividend", "quantity": 200,
        "price": 1.05, "trade_date": now_iso()[:10], "currency": "USD",
    }
    r = api_post("/portfolio/trades", trade3)
    check("JPM dividend accepted", r.status_code in (200, 201), f"got {r.status_code}: {r.text[:200]}")

    # List trades
    r = api_get("/portfolio/trades", limit=10)
    if r.status_code == 200:
        trades_list = get_json(r.json())
        check(f"Trades list has 3 entries (got {len(trades_list)})", len(trades_list) >= 3)
    else:
        skip("Trade listing", f"got {r.status_code}")

    # Verify AAPL quantity updated
    if buy_ok:
        r = api_get("/portfolio/holdings")
        if r.status_code == 200:
            items = get_json(r.json())
            for h in items:
                if h["ticker"] == "AAPL":
                    qty = h.get("quantity", 0)
                    check(f"AAPL quantity now 200 (got {qty})", abs(qty - 200) < 0.01)
                    cost = h.get("avg_cost_basis", h.get("cost_basis", 0))
                    expected_cost = (150 * 145.20 + 50 * 180.00) / 200
                    check(f"AAPL avg cost ~{expected_cost:.2f} (got {cost:.2f})",
                          abs(cost - expected_cost) < 1.0)
                    # Now verify market_value (should be set after trade)
                    mv = h.get("market_value") or 0
                    expected_mv = 200 * 178.50  # 200 shares after buy
                    check(f"AAPL market_value = {expected_mv} (got {mv})",
                          abs(float(mv) - expected_mv) < 1.0,
                          f"got {mv}")
                    break
            HOLDING_MAP.update({h["ticker"]: h["id"] for h in items})


# ---------------------------------------------------------------------------
# Phase 2: Classification
# ---------------------------------------------------------------------------
def phase_2():
    section("Phase 2 -- Classification Agent")

    r = api_post("/agents/classification/run")
    check("Classification trigger accepted", r.status_code in (200, 202),
          f"got {r.status_code}: {r.text[:200]}")

    print("  Waiting for classification to complete...")
    result = wait_for_agent("classification")
    if result:
        status = result.get("status", "")
        check(f"Classification completed (status={status})", status == "completed")
    else:
        check("Classification completed within timeout", False, "timed out")

    # Verify securities got classified
    r = api_get("/portfolio/holdings", limit=50)
    if r.status_code == 200:
        items = get_json(r.json())
        classified = sum(1 for h in items if h.get("sector") and h["sector"] != "Unknown")
        check(f"Holdings with sector: {classified}/15", classified >= 10,
              f"only {classified} classified")


# ---------------------------------------------------------------------------
# Phase 3: Event Injection (via Docker exec)
# ---------------------------------------------------------------------------
def phase_3():
    section("Phase 3.1 -- Create Test Source")

    source_body = {
        "name": "Test News Feed",
        "domain": "test.example.com",
        "source_type": "manual",
        "parser_id": "rss_generic",
    }
    r = api_post("/sources", source_body)
    source_id = None
    if r.status_code in (200, 201):
        source_id = r.json().get("id")
        check("Test source created", bool(source_id))
    else:
        check("Test source created", False, f"got {r.status_code}: {r.text[:200]}")
        source_id = str(uuid.uuid4())

    section("Phase 3.2 -- Inject Events via Docker")

    # Build the injection script
    events_json = json.dumps(TEST_EVENTS)
    holding_map_json = json.dumps(HOLDING_MAP)

    inject_script = f"""
import sqlite3, hashlib, uuid, json
from datetime import datetime, timedelta, timezone

conn = sqlite3.connect('/data/db/kleitos.db')
cursor = conn.cursor()

events = json.loads('''{events_json}''')
holding_map = json.loads('''{holding_map_json}''')
source_id = '{source_id}'
now = datetime.now(timezone.utc).isoformat()

injected = 0
event_ids = []

for evt in events:
    event_id = str(uuid.uuid4())
    event_ids.append(event_id)
    days_ago = evt.get('days_ago', 0)
    published = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    dedup = hashlib.sha256(f"{{evt['title']}}|{{published[:10]}}".encode()).hexdigest()[:32]

    try:
        cursor.execute(
            "INSERT INTO events (id, source_id, title, summary, event_type, "
            "materiality, confidence, published_at, fetched_at, dedup_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'unscored', 'unscored', ?, ?, ?, ?)",
            (event_id, source_id, evt['title'], evt.get('summary', ''),
             evt['event_type'], published, now, dedup, now),
        )

        for ticker in evt['tickers']:
            # Link for API filtering (link_type=ticker, link_target=TICKER)
            link_id1 = str(uuid.uuid4())
            cursor.execute(
                "INSERT INTO event_links (id, event_id, link_type, link_target, "
                "relevance_score, created_at) VALUES (?, ?, 'ticker', ?, 1.0, ?)",
                (link_id1, event_id, ticker, now),
            )

            # Link for analysis agent (link_type=ticker_match, link_target=holding_id)
            holding_id = holding_map.get(ticker)
            if holding_id:
                link_id2 = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO event_links (id, event_id, link_type, link_target, "
                    "relevance_score, created_at) VALUES (?, ?, 'ticker_match', ?, 1.0, ?)",
                    (link_id2, event_id, holding_id, now),
                )

        injected += 1
    except Exception as e:
        print(f'WARN: {{e}}')

conn.commit()
conn.close()
print(f'INJECTED:{{injected}}')
print(f'EVENT_IDS:{{json.dumps(event_ids)}}')
"""

    out = docker_exec_python(inject_script)
    injected_match = [line for line in out.split("\n") if line.startswith("INJECTED:")]
    injected_count = int(injected_match[0].split(":")[1]) if injected_match else 0
    check(f"Injected {injected_count}/{len(TEST_EVENTS)} events", injected_count == len(TEST_EVENTS),
          f"output: {out[:200]}")

    # Parse event IDs for later use
    global EVENT_IDS
    ids_match = [line for line in out.split("\n") if line.startswith("EVENT_IDS:")]
    if ids_match:
        try:
            EVENT_IDS = json.loads(ids_match[0].split(":", 1)[1])
        except json.JSONDecodeError:
            EVENT_IDS = []

    section("Phase 3.3 -- Verify Events in API")

    # Small delay to ensure WAL is flushed
    time.sleep(1)

    r = api_get("/events", limit=50)
    check("Events endpoint returns 200", r.status_code == 200)
    if r.status_code == 200:
        items = get_json(r.json())
        check(f"Events found (got {len(items)})", len(items) >= 15,
              f"got {len(items)}")

    # Filter by ticker
    r = api_get("/events", ticker="AAPL")
    if r.status_code == 200:
        items = get_json(r.json())
        check(f"AAPL events found (got {len(items)})", len(items) >= 1)
    else:
        check("AAPL events filter", False, f"got {r.status_code}")

    # Filter by event_type
    r = api_get("/events", event_type="earnings")
    if r.status_code == 200:
        items = get_json(r.json())
        check(f"Earnings events: {len(items)} (expected 4)", len(items) >= 4)

    # Filter by event_type = macro
    r = api_get("/events", event_type="macro")
    if r.status_code == 200:
        items = get_json(r.json())
        check(f"Macro events: {len(items)} (expected 3)", len(items) >= 3)

    # Single event detail
    if EVENT_IDS:
        r = api_get(f"/events/{EVENT_IDS[0]}")
        check("Single event detail returns 200", r.status_code == 200)
        if r.status_code == 200:
            body = r.json()
            check("Event has linked_tickers", len(body.get("linked_tickers", [])) > 0)
            check("Event has links", len(body.get("links", [])) > 0)


# ---------------------------------------------------------------------------
# Phase 4: Agent Pipeline
# ---------------------------------------------------------------------------
def phase_4():
    section("Phase 4.1 -- Analysis Agent")

    r = api_post("/agents/analysis/run")
    check("Analysis trigger accepted", r.status_code in (200, 202),
          f"got {r.status_code}: {r.text[:200]}")

    print("  Waiting for analysis to complete...")
    result = wait_for_agent("analysis", timeout=90)
    if result:
        status = result.get("status", "")
        check(f"Analysis completed (status={status})", status == "completed")
    else:
        check("Analysis completed within timeout", False, "timed out")

    # Give a moment for any async commits
    time.sleep(2)

    # Verify analysis notes (the ground truth — more reliable than run summary)
    r = api_get("/analysis/notes", limit=100)
    check("Analysis notes endpoint returns 200", r.status_code == 200)
    if r.status_code == 200:
        items = get_json(r.json())
        impact_notes = [n for n in items if n.get("note_type") == "impact_analysis"]
        sector_notes = [n for n in items if n.get("note_type") == "sector_impact"]
        check(f"Impact analysis notes created: {len(impact_notes)}", len(impact_notes) > 0)
        check(f"Sector impact notes created: {len(sector_notes)}", len(sector_notes) > 0)
        print(f"  INFO: Total notes: {len(items)} (impact={len(impact_notes)}, sector={len(sector_notes)})")

        # Check impact categories in at least one note
        for note in items:
            content = note.get("content", "{}")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except json.JSONDecodeError:
                    content = {}
            if content.get("materiality"):
                has_cats = all(
                    content.get(f) is not None
                    for f in ["materiality", "thesis_impact", "earnings_impact"]
                )
                check("Impact categories populated", has_cats,
                      f"fields: {list(content.keys())[:8]}")
                break

    section("Phase 4.2 -- Coverage QA Agent")

    r = api_post("/agents/coverage_qa/run")
    check("Coverage QA trigger accepted", r.status_code in (200, 202),
          f"got {r.status_code}: {r.text[:200]}")

    print("  Waiting for coverage QA to complete...")
    result = wait_for_agent("coverage_qa")
    if result:
        status = result.get("status", "")
        check(f"Coverage QA completed (status={status})", status == "completed",
              f"got {status}")
    else:
        check("Coverage QA completed within timeout", False, "timed out")

    # Verify via coverage_gap alerts (more reliable than run summary)
    r = api_get("/alerts", alert_type="coverage_gap")
    if r.status_code == 200:
        items = get_json(r.json())
        coverage_alerts = [a for a in items if a.get("alert_type") == "coverage_gap"]
        check(f"Coverage gap alerts created: {len(coverage_alerts)}", len(coverage_alerts) > 0)
    else:
        # If no alert_type filter, check all alerts
        r = api_get("/alerts")
        if r.status_code == 200:
            items = get_json(r.json())
            coverage_alerts = [a for a in items if a.get("alert_type") == "coverage_gap"]
            check(f"Coverage gap alerts created: {len(coverage_alerts)}", len(coverage_alerts) > 0)

    section("Phase 4.3 -- Risk Agent")

    r = api_post("/agents/risk/run")
    check("Risk trigger accepted", r.status_code in (200, 202),
          f"got {r.status_code}: {r.text[:200]}")

    print("  Waiting for risk agent to complete...")
    result = wait_for_agent("risk", timeout=60)
    if result:
        status = result.get("status", "")
        check(f"Risk agent completed (status={status})", status == "completed")
    else:
        check("Risk agent completed within timeout", False, "timed out")

    # Verify risk alerts
    r = api_get("/alerts")
    if r.status_code == 200:
        items = get_json(r.json())
        alert_types = [a.get("alert_type", "") for a in items]
        alert_type_set = set(alert_types)
        print(f"  INFO: Alert types found: {alert_type_set}")
        print(f"  INFO: Total alerts: {len(items)}")
        check(f"Risk/coverage alerts created ({len(items)})", len(items) > 0)

        # Check for concentration alerts
        has_concentration = any("concentration" in t for t in alert_types)
        check("Concentration alert present", has_concentration,
              f"types: {alert_type_set}")

        # Check for coverage gap alerts
        has_coverage = any("coverage_gap" in t for t in alert_types)
        check("Coverage gap alert present", has_coverage,
              f"types: {alert_type_set}")

    section("Phase 4.4 -- Digest Generation")

    r = api_post("/digests/generate", {"digest_type": "daily"})
    check("Digest generation accepted", r.status_code in (200, 202),
          f"got {r.status_code}: {r.text[:200]}")

    time.sleep(5)
    r = api_get("/digests/latest")
    check("Latest digest returns 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        body = r.json()
        sections = body.get("sections", [])
        check(f"Digest has sections ({len(sections)})", len(sections) > 0,
              f"keys: {list(body.keys())}")
        holding_count = body.get("holding_count", 0)
        check(f"Digest holding_count > 0 (got {holding_count})", holding_count > 0)


# ---------------------------------------------------------------------------
# Phase 5: Edge Cases & Error Handling
# ---------------------------------------------------------------------------
def phase_5():
    section("Phase 5.1 -- Trade Edge Cases")

    # Oversell
    r = api_post("/portfolio/trades", {
        "ticker": "AAPL", "trade_type": "sell", "quantity": 99999,
        "price": 180.00, "trade_date": now_iso()[:10],
    })
    check("Oversell rejected", r.status_code in (400, 422),
          f"got {r.status_code}: {r.text[:200]}")

    # Sell unknown ticker
    r = api_post("/portfolio/trades", {
        "ticker": "ZZZZZ", "trade_type": "sell", "quantity": 10,
        "price": 50.00, "trade_date": now_iso()[:10],
    })
    check("Sell unknown ticker rejected", r.status_code in (400, 404, 422),
          f"got {r.status_code}: {r.text[:200]}")

    # Zero quantity
    r = api_post("/portfolio/trades", {
        "ticker": "AAPL", "trade_type": "buy", "quantity": 0,
        "price": 180.00, "trade_date": now_iso()[:10],
    })
    check("Zero quantity rejected", r.status_code == 422,
          f"got {r.status_code}: {r.text[:200]}")

    # Negative price
    r = api_post("/portfolio/trades", {
        "ticker": "AAPL", "trade_type": "buy", "quantity": 10,
        "price": -5.00, "trade_date": now_iso()[:10],
    })
    check("Negative price rejected", r.status_code == 422,
          f"got {r.status_code}: {r.text[:200]}")

    # Invalid trade type
    r = api_post("/portfolio/trades", {
        "ticker": "AAPL", "trade_type": "swap", "quantity": 10,
        "price": 180.00, "trade_date": now_iso()[:10],
    })
    check("Invalid trade type rejected", r.status_code == 422,
          f"got {r.status_code}: {r.text[:200]}")

    section("Phase 5.2 -- Upload Edge Cases")

    # Non-CSV file
    files = {"file": ("data.json", '{"foo":"bar"}', "application/json")}
    r = api("POST", "/portfolio/upload", files=files)
    check("Non-CSV upload rejected", r.status_code in (400, 415, 422),
          f"got {r.status_code}")

    # Empty file
    files = {"file": ("empty.csv", "", "text/csv")}
    r = api("POST", "/portfolio/upload", files=files)
    check("Empty CSV upload handled", r.status_code in (200, 400, 422),
          f"got {r.status_code}")

    section("Phase 5.3 -- API Error Handling")

    r = api_get("/portfolio/holdings/nonexistent-uuid-12345")
    check("Invalid holding ID returns 404", r.status_code == 404,
          f"got {r.status_code}")

    r = api_get("/events/nonexistent-uuid-12345")
    check("Invalid event ID returns 404", r.status_code == 404,
          f"got {r.status_code}")

    r = api_post("/agents/nonexistent_agent/run")
    check("Invalid agent returns 404", r.status_code == 404,
          f"got {r.status_code}")

    section("Phase 5.4 -- Alert Lifecycle")

    r = api_get("/alerts/active")
    active_ok = r.status_code == 200
    check("Active alerts endpoint returns 200", active_ok)

    if active_ok:
        items = get_json(r.json())
        if items:
            # Acknowledge one
            alert_id = items[0].get("id")
            r = api_post(f"/alerts/{alert_id}/acknowledge")
            check("Acknowledge alert returns 200", r.status_code == 200,
                  f"got {r.status_code}: {r.text[:200]}")

            # Double acknowledge
            r = api_post(f"/alerts/{alert_id}/acknowledge")
            check("Double acknowledge returns 409", r.status_code == 409,
                  f"got {r.status_code}")

            # Delete an alert
            if len(items) > 1:
                del_id = items[1].get("id")
                r = api_delete(f"/alerts/{del_id}")
                check("Delete alert returns 200", r.status_code == 200,
                      f"got {r.status_code}: {r.text[:200]}")

            # Bulk acknowledge
            r = api_post("/alerts/acknowledge-all")
            check("Bulk acknowledge returns 200", r.status_code == 200,
                  f"got {r.status_code}: {r.text[:200]}")
            if r.status_code == 200:
                body = r.json()
                ack_count = body.get("acknowledged_count", 0)
                print(f"  INFO: Bulk acknowledged {ack_count} alerts")
        else:
            skip("Alert lifecycle", "no active alerts")

    # Acknowledge invalid ID
    r = api_post("/alerts/nonexistent-uuid-12345/acknowledge")
    check("Acknowledge invalid ID returns 404", r.status_code == 404,
          f"got {r.status_code}")

    section("Phase 5.5 -- Agent Resilience")

    r = api_get("/agents/status")
    check("Agent status returns 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        if isinstance(body, dict):
            agent_names = set(body.keys())
        elif isinstance(body, list):
            agent_names = {a.get("agent_id", a.get("name", "")) for a in body}
        else:
            agent_names = set()
        expected = {"collection", "analysis", "classification", "coverage_qa", "risk", "intake"}
        missing = expected - agent_names
        check(f"All 6 agents registered (missing: {missing or 'none'})",
              len(missing) == 0, f"found: {agent_names}")


# ---------------------------------------------------------------------------
# Phase 6: Dashboard
# ---------------------------------------------------------------------------
def phase_6():
    section("Phase 6 -- Dashboard")

    r = requests.get(f"{BASE_URL}/dashboard", timeout=10, allow_redirects=True)
    check("Dashboard page loads", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        check("Dashboard returns HTML", "<html" in r.text.lower() or "<!doctype" in r.text.lower())
        check("Dashboard includes JavaScript", "<script" in r.text.lower())
        check("Dashboard has tab structure",
              any(t in r.text.lower() for t in ["tab", "tabpanel", "portfolio"]))


# ---------------------------------------------------------------------------
# Phase 7: Audit Trail & Final Integrity
# ---------------------------------------------------------------------------
def phase_7():
    section("Phase 7 -- Audit Trail & Final Checks")

    r = api_get("/audit", limit=50)
    check("Audit log returns 200", r.status_code == 200)
    if r.status_code == 200:
        items = get_json(r.json())
        check(f"Audit entries exist ({len(items)})", len(items) > 0)
        actions = {e.get("action", "") for e in items}
        print(f"  INFO: Audit actions: {actions}")

    # Final holdings integrity
    r = api_get("/portfolio/holdings", limit=50)
    if r.status_code == 200:
        items = get_json(r.json())
        all_valid = True
        for h in items:
            qty = h.get("quantity", 0)
            if qty <= 0:
                all_valid = False
                print(f"  WARN: {h['ticker']} has quantity {qty}")
        check(f"All {len(items)} holdings have valid quantities", all_valid)

    # Verify sources
    r = api_get("/sources")
    check("Sources endpoint returns 200", r.status_code == 200)

    # Final event count
    r = api_get("/events", limit=1)
    check("Events still accessible", r.status_code == 200)

    # Final notes count
    r = api_get("/analysis/notes", limit=1)
    check("Analysis notes still accessible", r.status_code == 200)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
HOLDING_MAP: dict[str, str] = {}
EVENT_IDS: list[str] = []


def _update_api_key(key: str):
    global API_KEY
    if key:
        API_KEY = key


def main():
    global BASE_URL, CONTAINER_NAME

    parser = argparse.ArgumentParser(description="Axion full pipeline test")
    parser.add_argument("--base-url", default=BASE_URL, help="API base URL")
    parser.add_argument("--container", default=CONTAINER_NAME, help="Docker container name")
    parser.add_argument("--phase", type=int, default=0, help="Run only this phase (0=all)")
    parser.add_argument("--api-key", default=API_KEY, help="API key for auth")
    parser.add_argument("--no-reset", action="store_true", help="Skip database reset")
    args = parser.parse_args()

    BASE_URL = args.base_url
    CONTAINER_NAME = args.container
    _update_api_key(args.api_key)

    print("=" * 68)
    print("AXION -- COMPREHENSIVE PIPELINE TEST SUITE")
    print(f"  Target    : {BASE_URL}")
    print(f"  Container : {CONTAINER_NAME}")
    print(f"  Auth      : {'enabled' if API_KEY else 'disabled'}")
    print("=" * 68)

    phases = {
        0: ("Health Check", phase_0),
        1: ("Portfolio Setup", phase_1),
        2: ("Classification", phase_2),
        3: ("Event Injection", phase_3),
        4: ("Agent Pipeline", phase_4),
        5: ("Edge Cases", phase_5),
        6: ("Dashboard", phase_6),
        7: ("Audit Trail", phase_7),
    }

    if args.phase and args.phase in phases:
        if not phase_0():
            print("\nABORTED: Server not reachable")
            sys.exit(1)
        title, fn = phases[args.phase]
        fn()
    else:
        # Run all phases
        if not phase_0():
            print("\nABORTED: Server not reachable")
            sys.exit(1)

        # Reset database for clean state
        if not args.no_reset:
            reset_database()

        for p in range(1, 8):
            try:
                title, fn = phases[p]
                fn()
            except Exception as exc:
                print(f"\n  ERROR in Phase {p} ({title}): {exc}")
                import traceback
                traceback.print_exc()

    # Summary
    print("\n" + "=" * 68)
    print(f"RESULTS: {_pass} passed, {_fail} failed, {_skip} skipped "
          f"out of {_pass + _fail} tests")
    if _fail:
        print("\nFAILED TESTS:")
        for status, name, detail in _results:
            if status == "FAIL":
                print(f"  X {name}  -- {detail}")
    print("=" * 68)

    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
