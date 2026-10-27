"""Mini DevOps Deployment Dashboard: Flask app, JSON API and background scheduler."""
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from flask import Flask, abort, jsonify, render_template, request

import auth
import db
import faq
from config import Config
from monitor import github_client, health_checks, metrics

WINDOWS = {"1h": 3600, "24h": 86400, "7d": 7 * 86400}
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    path = lambda: app.config["DATABASE"]  # noqa: E731
    db.init_db(path())
    app.config["SECRET_KEY"] = auth.load_secret_key(app.config["SECRET_KEY"], os.path.dirname(os.path.abspath(path())))
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=app.config["SECURE_COOKIES"],
                      PERMANENT_SESSION_LIFETIME=7 * 86400)
    auth.seed_admin(app)
    app.register_blueprint(auth.bp)
    auth.install_guards(app)

    # ------------------------------------------------------------------ helpers
    def app_row(con, app_id):
        row = con.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
        if not row:
            abort(404, description="application not found")
        return row

    def summarize(con, a):
        recent = con.execute(
            "SELECT ok, status_code, response_ms, error, ts FROM checks WHERE app_id=? ORDER BY ts DESC LIMIT 48",
            (a["id"],)).fetchall()
        last_ok = con.execute("SELECT MAX(ts) t FROM checks WHERE app_id=? AND ok=1", (a["id"],)).fetchone()["t"]
        latest = recent[0] if recent else None
        return {
            "id": a["id"], "name": a["name"], "url": a["url"], "repo": a["repo"],
            "expected_status": a["expected_status"], "interval_s": a["interval_s"],
            "paused": bool(a["paused"]),
            "status": health_checks.derive_status(recent, app.config["SLOW_MS"], bool(a["paused"])),
            "latest": dict(latest) if latest else None,
            "last_success_ts": last_ok,
            "uptime": {k: health_checks.uptime_pct(con, a["id"], s) for k, s in WINDOWS.items()},
            "history": [{"ok": bool(r["ok"]), "ms": r["response_ms"]} for r in reversed(recent)],
        }

    def validate_app(data):
        name = (data.get("name") or "").strip()
        url = (data.get("url") or "").strip()
        repo = (data.get("repo") or "").strip() or None
        if not name or len(name) > 60:
            return None, "name is required (max 60 characters)"
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None, "url must start with http:// or https://"
        if repo and not github_client.REPO_RE.match(repo):
            return None, 'repo must look like "owner/name"'
        try:
            interval = int(data.get("interval_s") or app.config["DEFAULT_INTERVAL"])
            expected = int(data.get("expected_status") or 200)
        except (TypeError, ValueError):
            return None, "interval and expected status must be numbers"
        if not 5 <= interval <= 86400:
            return None, "interval must be between 5 and 86400 seconds"
        if not 100 <= expected <= 599:
            return None, "expected status must be a valid HTTP status code"
        return {"name": name, "url": url, "repo": repo, "interval_s": interval, "expected_status": expected}, None

    @app.errorhandler(404)
    @app.errorhandler(400)
    def _err(e):
        if request.path.startswith("/api/"):
            return jsonify(error=getattr(e, "description", "error")), e.code
        return e

    # -------------------------------------------------------------------- pages
    @app.get("/")
    def home():
        return render_template("home.html", version=app.config["APP_VERSION"], faqs=faq.FAQS, categories=faq.CATEGORIES)

    @app.get("/dashboard")
    @auth.login_required
    def dashboard():
        return render_template("dashboard.html", env_name=app.config["ENV_NAME"], version=app.config["APP_VERSION"])

    @app.get("/health")
    def health():
        return jsonify(status="ok", version=app.config["APP_VERSION"], env=app.config["ENV_NAME"])

    # --------------------------------------------------------- demo targets
    # Endpoints this app owns, so there is something real to monitor on first run.
    @app.get("/demo/ok")
    def demo_ok():
        return jsonify(status="ok")

    @app.get("/demo/slow")
    def demo_slow():
        time.sleep(random.uniform(0.35, 0.9))
        return jsonify(status="ok", note="deliberately slow")

    @app.get("/demo/flaky")
    def demo_flaky():
        if random.random() < 0.3:
            return jsonify(status="error"), 503
        return jsonify(status="ok")

    @app.get("/demo/down")
    def demo_down():
        return jsonify(status="error", note="always failing"), 500

    # ---------------------------------------------------------------- API: apps
    @app.get("/api/apps")
    def api_apps():
        with db.session(path()) as con:
            return jsonify([summarize(con, a) for a in con.execute("SELECT * FROM apps ORDER BY id")])

    @app.post("/api/apps")
    def api_apps_create():
        data, err = validate_app(request.get_json(silent=True) or {})
        if err:
            return jsonify(error=err), 400
        with db.session(path()) as con:
            cur = con.execute(
                "INSERT INTO apps (name,url,repo,expected_status,interval_s,created_at) VALUES (?,?,?,?,?,?)",
                (data["name"], data["url"], data["repo"], data["expected_status"], data["interval_s"], time.time()))
            db.add_log(con, "INFO", f"Registered application {data['name']} ({data['url']})", cur.lastrowid)
            a = app_row(con, cur.lastrowid)
        if not app.config.get("TESTING"):  # first check straight away so the card is not empty
            threading.Thread(target=health_checks.run_check, daemon=True,
                             args=(path(), a, app.config["CHECK_TIMEOUT"], app.config["SLOW_MS"])).start()
        return jsonify(id=a["id"]), 201

    @app.patch("/api/apps/<int:app_id>")
    def api_apps_update(app_id):
        body = request.get_json(silent=True) or {}
        with db.session(path()) as con:
            a = app_row(con, app_id)
            if "paused" in body:
                paused = int(bool(body["paused"]))
                con.execute("UPDATE apps SET paused=? WHERE id=?", (paused, app_id))
                db.add_log(con, "INFO", f"{a['name']}: {'maintenance started, checks paused' if paused else 'maintenance ended, checks resumed'}", app_id)
        return jsonify(ok=True)

    @app.delete("/api/apps/<int:app_id>")
    def api_apps_delete(app_id):
        with db.session(path()) as con:
            a = app_row(con, app_id)
            con.execute("DELETE FROM apps WHERE id=?", (app_id,))
            db.add_log(con, "INFO", f"Removed application {a['name']}")
        return jsonify(ok=True)

    @app.get("/api/apps/<int:app_id>/checks")
    def api_checks(app_id):
        window = WINDOWS.get(request.args.get("window", "1h"), 3600)
        with db.session(path()) as con:
            app_row(con, app_id)
            rows = con.execute(
                "SELECT ts, ok, status_code, response_ms, error FROM checks WHERE app_id=? AND ts>=? ORDER BY ts",
                (app_id, time.time() - window)).fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/check")
    def api_check_now():
        with db.session(path()) as con:
            apps = [a for a in con.execute("SELECT * FROM apps WHERE paused=0")]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda a: health_checks.run_check(path(), a, app.config["CHECK_TIMEOUT"], app.config["SLOW_MS"]), apps))
        return jsonify(checked=len(apps))

    # ----------------------------------------------------------- API: overview
    @app.get("/api/overview")
    def api_overview():
        with db.session(path()) as con:
            items = [summarize(con, a) for a in con.execute("SELECT * FROM apps")]
            n24 = con.execute("SELECT COUNT(*) n, COALESCE(SUM(ok),0) up FROM checks WHERE ts>=?", (time.time() - 86400,)).fetchone()
            ms = [r["response_ms"] for r in con.execute(
                "SELECT response_ms FROM checks WHERE ok=1 AND response_ms IS NOT NULL AND ts>=? ORDER BY response_ms", (time.time() - 86400,))]
        counts = {}
        for i in items:
            counts[i["status"]] = counts.get(i["status"], 0) + 1
        return jsonify(
            apps=len(items), healthy=counts.get("healthy", 0), counts=counts,
            uptime_24h=round(n24["up"] / n24["n"] * 100, 2) if n24["n"] else None,
            checks_24h=n24["n"], failures_24h=n24["n"] - n24["up"],
            p95_ms=round(ms[int(len(ms) * 0.95)] if len(ms) > 1 else (ms[0] if ms else 0)) if ms else None)

    # ------------------------------------------------------- API: deployments
    @app.get("/api/deployments")
    def api_deployments():
        with db.session(path()) as con:
            rows = con.execute(
                "SELECT d.*, a.name app_name FROM deployments d JOIN apps a ON a.id=d.app_id ORDER BY d.ts DESC LIMIT 25").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.post("/api/deployments")
    def api_deployments_create():
        b = request.get_json(silent=True) or {}
        sha, version = (b.get("commit_sha") or "").strip(), (b.get("version") or "").strip()
        status = (b.get("status") or "success").strip().lower()
        if not SHA_RE.match(sha):
            return jsonify(error="commit_sha must be a 7-40 character hex string"), 400
        if not version or len(version) > 40:
            return jsonify(error="version is required (max 40 characters)"), 400
        if status not in ("success", "failed"):
            return jsonify(error="status must be success or failed"), 400
        with db.session(path()) as con:
            a = None
            if b.get("app_id"):
                a = app_row(con, int(b["app_id"]))
            elif b.get("app"):
                a = con.execute("SELECT * FROM apps WHERE name=?", (b["app"],)).fetchone()
            if not a:
                return jsonify(error="unknown application (pass app_id or app name)"), 400
            con.execute(
                "INSERT INTO deployments (app_id, ts, version, commit_sha, status, note) VALUES (?,?,?,?,?,?)",
                (a["id"], time.time(), version, sha.lower(), status, (b.get("note") or "")[:200] or None))
            db.add_log(con, "INFO" if status == "success" else "ERROR",
                       f"{a['name']}: deployment {version} {'succeeded' if status == 'success' else 'failed'} (commit {sha[:7].lower()})", a["id"])
        return jsonify(ok=True), 201

    # ---------------------------------------------------------------- API: logs
    @app.get("/api/logs")
    def api_logs():
        q = (request.args.get("q") or "").strip()
        level = (request.args.get("level") or "").upper()
        sql, args = "SELECT l.*, a.name app_name FROM logs l LEFT JOIN apps a ON a.id=l.app_id WHERE 1=1", []
        if q:
            sql += " AND l.message LIKE ? ESCAPE '\\'"
            args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        if level in ("INFO", "WARN", "ERROR"):
            sql += " AND l.level=?"
            args.append(level)
        sql += " ORDER BY l.ts DESC, l.id DESC LIMIT 100"
        with db.session(path()) as con:
            return jsonify([dict(r) for r in con.execute(sql, args)])

    # ------------------------------------------------------- API: github, env
    @app.get("/api/apps/<int:app_id>/github")
    def api_github(app_id):
        with db.session(path()) as con:
            a = app_row(con, app_id)
        if not a["repo"]:
            return jsonify(error="no repository linked to this application"), 404
        try:
            return jsonify(github_client.repo_summary(a["repo"], app.config["GITHUB_TOKEN"]))
        except github_client.GitHubError as e:
            return jsonify(error=str(e)), 502

    @app.get("/api/environment")
    def api_env():
        m = metrics.host_metrics()
        m.update(env_name=app.config["ENV_NAME"], app_version=app.config["APP_VERSION"],
                 database="SQLite", check_timeout=app.config["CHECK_TIMEOUT"], slow_ms=app.config["SLOW_MS"],
                 scheduler=app.config["SCHEDULER"], github_token=bool(app.config["GITHUB_TOKEN"]))
        return jsonify(m)

    # ---------------------------------------------------------------- scheduler
    def seed_demo():
        with db.session(path()) as con:
            if con.execute("SELECT COUNT(*) n FROM apps").fetchone()["n"]:
                return
            base = f"http://127.0.0.1:{app.config['PORT']}"
            now = time.time()
            for name, ep, exp in [("Dashboard (self)", "/health", 200), ("Demo · fast API", "/demo/ok", 200),
                                  ("Demo · slow service", "/demo/slow", 200), ("Demo · flaky worker", "/demo/flaky", 200),
                                  ("Demo · broken endpoint", "/demo/down", 200)]:
                con.execute("INSERT INTO apps (name,url,expected_status,interval_s,created_at) VALUES (?,?,?,?,?)",
                            (name, base + ep, exp, app.config["DEFAULT_INTERVAL"], now))
            db.add_log(con, "INFO", "Registered built-in demo targets served by this dashboard (real checks, local endpoints)")

    def scheduler_loop():
        pool = ThreadPoolExecutor(max_workers=8)
        last_prune = 0
        time.sleep(3)  # let the web server start listening before the first self-checks
        while True:
            try:
                now = time.time()
                with db.session(path()) as con:
                    due = con.execute(
                        """SELECT a.* FROM apps a WHERE a.paused=0 AND
                           COALESCE((SELECT MAX(ts) FROM checks c WHERE c.app_id=a.id),0) + a.interval_s <= ?""", (now,)).fetchall()
                    if now - last_prune > 3600:
                        con.execute("DELETE FROM checks WHERE ts<?", (now - app.config["RETENTION_DAYS"] * 86400,))
                        con.execute("DELETE FROM logs WHERE ts<?", (now - app.config["RETENTION_DAYS"] * 86400,))
                        last_prune = now
                for a in due:
                    pool.submit(health_checks.run_check, path(), a, app.config["CHECK_TIMEOUT"], app.config["SLOW_MS"])
            except Exception as e:  # keep the loop alive
                print("scheduler error:", e)
            time.sleep(3)

    if app.config["SEED_DEMO"] and not app.config.get("TESTING"):
        seed_demo()
    if app.config["SCHEDULER"] and not app.config.get("TESTING"):
        threading.Thread(target=scheduler_loop, daemon=True, name="scheduler").start()
    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=Config.PORT, threaded=True, use_reloader=False)
