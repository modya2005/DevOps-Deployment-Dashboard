"""HTTP health checks, status derivation and uptime calculation.

Rules (also shown in the dashboard):
  * A check passes when the response status equals the app's expected status
    and the request finished before the timeout.
  * Timeouts and connection errors count as failures.
  * Paused apps (maintenance) are skipped: no check is made and none is counted.
  * Uptime = successful eligible checks / total eligible checks * 100 for the period.
"""
import time

import requests

import db


def check_url(url, expected_status=200, timeout=5):
    """Make one request. Returns a dict ready to store; never raises."""
    started = time.perf_counter()
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True,
                         headers={"User-Agent": "mini-devops-dashboard/1.0"})
        ms = (time.perf_counter() - started) * 1000
        ok = r.status_code == expected_status
        err = None if ok else f"expected HTTP {expected_status}, got {r.status_code}"
        return {"ok": ok, "status_code": r.status_code, "response_ms": round(ms, 1), "error": err}
    except requests.exceptions.Timeout:
        return {"ok": False, "status_code": None, "response_ms": None, "error": f"timeout after {timeout}s"}
    except requests.exceptions.ConnectionError as e:
        return {"ok": False, "status_code": None, "response_ms": None,
                "error": "connection failed: " + str(e).split(":")[-1].strip()[:120]}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "status_code": None, "response_ms": None, "error": f"request error: {e}"[:160]}


def derive_status(recent, slow_ms=500, paused=False):
    """recent: newest-first list of check rows/dicts. Status is derived, never stored."""
    if paused:
        return "paused"
    if not recent:
        return "unknown"
    last = recent[0]
    if not last["ok"]:
        if len(recent) > 1 and not recent[1]["ok"]:
            return "down"
        return "degraded"
    if last["response_ms"] is not None and last["response_ms"] > slow_ms:
        return "slow"
    return "healthy"


def uptime_pct(con, app_id, seconds):
    row = con.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(ok),0) up FROM checks WHERE app_id=? AND ts>=?",
        (app_id, time.time() - seconds)).fetchone()
    if not row["n"]:
        return None
    return round(row["up"] / row["n"] * 100, 2)


def run_check(path, app, timeout, slow_ms):
    """Check one app, store the result, and write log lines for notable events."""
    res = check_url(app["url"], app["expected_status"], timeout)
    now = time.time()
    with db.session(path) as con:
        prev = con.execute("SELECT ok FROM checks WHERE app_id=? ORDER BY ts DESC LIMIT 1",
                           (app["id"],)).fetchone()
        con.execute(
            "INSERT INTO checks (app_id, ts, ok, status_code, response_ms, error) VALUES (?,?,?,?,?,?)",
            (app["id"], now, int(res["ok"]), res["status_code"], res["response_ms"], res["error"]))
        name = app["name"]
        if not res["ok"]:
            db.add_log(con, "ERROR", f"{name}: health check failed ({res['error']})", app["id"], now)
        elif res["response_ms"] and res["response_ms"] > slow_ms:
            db.add_log(con, "WARN", f"{name}: slow response {res['response_ms']:.0f} ms (threshold {slow_ms} ms)", app["id"], now)
        if prev is not None and prev["ok"] == 0 and res["ok"]:
            db.add_log(con, "INFO", f"{name}: recovered, responding with HTTP {res['status_code']}", app["id"], now)
    return res
