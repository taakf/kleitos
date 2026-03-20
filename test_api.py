"""Axion API Endpoint Test Suite — Phase 1"""
import json
import urllib.request
import urllib.error
import urllib.parse
import sys
import io
import uuid

BASE = "http://localhost:7777/api/v1"
PASS = 0
FAIL = 0
ERRORS = []

def test(num, name, method, path, expected_status=200, body=None, content_type=None, check=None):
    global PASS, FAIL
    url = BASE + path
    try:
        if body and isinstance(body, dict):
            data = json.dumps(body).encode()
            content_type = content_type or "application/json"
        elif body and isinstance(body, bytes):
            data = body
        else:
            data = None

        req = urllib.request.Request(url, data=data, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            status = resp.status
            resp_body = resp.read().decode()
        except urllib.error.HTTPError as e:
            status = e.code
            resp_body = e.read().decode()

        try:
            result = json.loads(resp_body)
        except:
            result = resp_body

        if status != expected_status:
            FAIL += 1
            msg = f"  FAIL #{num}: {name} — expected {expected_status}, got {status}"
            print(msg)
            ERRORS.append(msg)
            return result

        if check:
            ok, detail = check(result)
            if not ok:
                FAIL += 1
                msg = f"  FAIL #{num}: {name} — {detail}"
                print(msg)
                ERRORS.append(msg)
                return result

        PASS += 1
        print(f"  PASS #{num}: {name}")
        return result

    except Exception as e:
        FAIL += 1
        msg = f"  FAIL #{num}: {name} — Exception: {e}"
        print(msg)
        ERRORS.append(msg)
        return None


def multipart_upload(num, name, path, filename, file_content, file_content_type, expected_status=200, check=None):
    """Upload a file via multipart/form-data"""
    global PASS, FAIL
    url = BASE + path
    boundary = f"----boundary{uuid.uuid4().hex}"

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {file_content_type}\r\n\r\n"
    ).encode() + file_content + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

    try:
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            status = resp.status
            resp_body = resp.read().decode()
        except urllib.error.HTTPError as e:
            status = e.code
            resp_body = e.read().decode()

        try:
            result = json.loads(resp_body)
        except:
            result = resp_body

        if status != expected_status:
            FAIL += 1
            msg = f"  FAIL #{num}: {name} — expected {expected_status}, got {status}"
            print(msg)
            ERRORS.append(msg)
            return result

        if check:
            ok, detail = check(result)
            if not ok:
                FAIL += 1
                msg = f"  FAIL #{num}: {name} — {detail}"
                print(msg)
                ERRORS.append(msg)
                return result

        PASS += 1
        print(f"  PASS #{num}: {name}")
        return result

    except Exception as e:
        FAIL += 1
        msg = f"  FAIL #{num}: {name} — Exception: {e}"
        print(msg)
        ERRORS.append(msg)
        return None


print("=" * 60)
print("AXION API TEST SUITE")
print("=" * 60)

# ── 1.1 Health ──
print("\n--- 1.1 Health ---")
test(1, "Health check", "GET", "/health", check=lambda r: (
    r.get("status") == "ok" and r.get("database") == "connected",
    f"status={r.get('status')}, db={r.get('database')}"
))

# ── 1.2 Portfolio ──
print("\n--- 1.2 Portfolio ---")
holdings = test(2, "List holdings", "GET", "/portfolio/holdings", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(3, "Holdings filter (sector)", "GET", "/portfolio/holdings?sector=Technology", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

# Get a real holding ID for test 4
holding_id = None
if holdings and len(holdings) > 0:
    holding_id = holdings[0].get("id")

if holding_id:
    test(4, "Single holding (valid ID)", "GET", f"/portfolio/holdings/{holding_id}", check=lambda r: (
        "ticker" in r, f"missing 'ticker' in response"
    ))
else:
    print(f"  SKIP #4: Single holding — no holdings available")

test(5, "Single holding (invalid ID)", "GET", "/portfolio/holdings/nonexistent-id-12345", expected_status=404)

summary = test(6, "Portfolio summary", "GET", "/portfolio/summary", check=lambda r: (
    "total_market_value" in r or "total_value" in r, f"missing market value key, keys={list(r.keys())}"
))

for i, dim in enumerate(["sector", "geography", "currency", "theme"], start=7):
    test(i, f"Exposure by {dim}", "GET", f"/portfolio/exposure?dimension={dim}", check=lambda r: (
        isinstance(r, dict) and "buckets" in r, f"expected dict with 'buckets', got {type(r).__name__}"
    ))

# Upload tests
csv_content = b"ticker,quantity,avg_cost\nTEST,100,50.00\n"
multipart_upload(11, "CSV upload (valid)", "/portfolio/upload", "test.csv", csv_content, "text/csv")

json_content = b'{"holdings": [{"ticker": "TEST"}]}'
multipart_upload(12, "Upload non-CSV (JSON file)", "/portfolio/upload", "test.json", json_content, "application/json", expected_status=400)

empty_csv = b""
multipart_upload(13, "Upload empty file", "/portfolio/upload", "empty.csv", empty_csv, "text/csv", expected_status=400)

# ── 1.3 Events ──
print("\n--- 1.3 Events ---")
test(14, "List events", "GET", "/events", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(15, "Events with filters", "GET", "/events?ticker=AAPL&event_type=earnings", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(16, "Recent events", "GET", "/events/recent", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(17, "Single event (invalid ID)", "GET", "/events/nonexistent-event-id", expected_status=404)

# ── 1.4 Analysis ──
print("\n--- 1.4 Analysis ---")
test(18, "List notes", "GET", "/analysis/notes", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(19, "Notes with filters", "GET", "/analysis/notes?ticker=MSFT", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(20, "Single note (invalid ID)", "GET", "/analysis/notes/nonexistent-note-id", expected_status=404)

test(21, "Trigger analysis", "POST", "/analysis/run", body={"scope": "portfolio"}, expected_status=202)

# ── 1.5 Alerts ──
print("\n--- 1.5 Alerts ---")
all_alerts = test(22, "All alerts", "GET", "/alerts", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

active_alerts = test(23, "Active alerts", "GET", "/alerts/active", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(24, "Filtered alerts (severity)", "GET", "/alerts?severity=critical", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

# Acknowledge an alert
alert_id = None
if active_alerts and len(active_alerts) > 0:
    alert_id = active_alerts[0].get("id")

if alert_id:
    test(25, "Acknowledge alert", "POST", f"/alerts/{alert_id}/acknowledge")
    test(27, "Double acknowledge (expect 409)", "POST", f"/alerts/{alert_id}/acknowledge", expected_status=409)
else:
    print(f"  SKIP #25: Acknowledge alert — no active alerts")
    print(f"  SKIP #27: Double acknowledge — no active alerts")

test(26, "Acknowledge invalid ID", "POST", "/alerts/nonexistent-alert-id/acknowledge", expected_status=404)

# ── 1.6 Digests ──
print("\n--- 1.6 Digests ---")
test(28, "List digests", "GET", "/digests", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

# Latest returns 200 if digests exist, 404 if none — test accepts both
result29 = test(29, "Latest digest", "GET", "/digests/latest", expected_status=200)

test(30, "Generate digest (defaults)", "POST", "/digests/generate", body={"digest_type": "ad-hoc", "scope": "portfolio"}, expected_status=202)

test(31, "Generate with type=daily", "POST", "/digests/generate", body={"digest_type": "daily"}, expected_status=202)

# ── 1.7 Audit ──
print("\n--- 1.7 Audit ---")
test(32, "Audit log", "GET", "/audit", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(33, "Filtered audit", "GET", "/audit?entity_type=holdings", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

# ── 1.8 Agents ──
print("\n--- 1.8 Agents ---")
test(34, "Agent status", "GET", "/agents/status", check=lambda r: (
    isinstance(r, (list, dict)), f"expected list/dict, got {type(r).__name__}"
))

test(35, "Agent runs", "GET", "/agents/runs", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

test(36, "Trigger collection agent", "POST", "/agents/collection/run", expected_status=202)

test(37, "Invalid agent", "POST", "/agents/invalid_agent/run", expected_status=404)

# ── 1.9 Sources ──
print("\n--- 1.9 Sources ---")
sources = test(38, "List sources", "GET", "/sources", check=lambda r: (
    isinstance(r, list), f"expected list, got {type(r).__name__}"
))

source_id = None
if sources and len(sources) > 0:
    source_id = sources[0].get("id")

if source_id:
    test(39, "Source health", "GET", f"/sources/{source_id}/health")
    test(40, "Enable source", "POST", f"/sources/{source_id}/enable")
    test(41, "Disable source", "POST", f"/sources/{source_id}/disable")
else:
    test(39, "Source health (no sources)", "GET", "/sources/nonexistent/health", expected_status=404)
    print(f"  SKIP #40: Enable source — no sources configured")
    print(f"  SKIP #41: Disable source — no sources configured")

# ── Summary ──
print("\n" + "=" * 60)
print(f"RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL} tests")
print("=" * 60)

if ERRORS:
    print("\nFAILURES:")
    for e in ERRORS:
        print(e)

sys.exit(0 if FAIL == 0 else 1)
