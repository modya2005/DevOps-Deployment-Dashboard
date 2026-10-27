"""Authentication: users, sessions, CSRF protection, login rate limiting and request guards.

Passwords are stored as salted PBKDF2 hashes (werkzeug). Sessions are signed cookies
(HttpOnly, SameSite=Lax). Forms carry a CSRF token; JSON API calls are protected by the
SameSite cookie plus an Origin check on every state-changing request.
"""
import re
import secrets
import time
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (Blueprint, current_app, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import db

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MAX_FAILS, WINDOW_S = 8, 600
_fails = {}   # ip -> [timestamps of failed logins]
_DUMMY_HASH = generate_password_hash("not-a-real-password")


# ----------------------------------------------------------------- helpers
def load_secret_key(configured, instance_dir):
    """Use SECRET_KEY if set, else create one once and keep it next to the database."""
    if configured:
        return configured
    f = Path(instance_dir) / "secret_key"
    if f.exists():
        return f.read_text().strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    f.write_text(key)
    try:
        f.chmod(0o600)
    except OSError:
        pass
    return key


def user_count():
    with db.session(current_app.config["DATABASE"]) as con:
        return con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def signup_open():
    return user_count() == 0 or current_app.config["ALLOW_SIGNUP"]


def _eq(a, b):
    """Constant-time comparison that is safe for non-ASCII input."""
    return secrets.compare_digest(str(a).encode(), str(b).encode())


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def safe_next(target):
    """Only allow same-site relative paths, never //host or absolute URLs."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("dashboard")


def current_user():
    return session.get("user")


def create_user(path, username, password):
    with db.session(path) as con:
        con.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                    (username, generate_password_hash(password), time.time()))
        db.add_log(con, "INFO", f"Account created: {username}")


def seed_admin(app):
    u, p = app.config["ADMIN_USER"], app.config["ADMIN_PASSWORD"]
    if not (u and p and USERNAME_RE.match(u) and len(p) >= 8):
        return
    with db.session(app.config["DATABASE"]) as con:
        if con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] == 0:
            con.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
                        (u, generate_password_hash(p), time.time()))


def _rate_limited():
    now = time.time()
    hits = [t for t in _fails.get(request.remote_addr, []) if now - t < WINDOW_S]
    _fails[request.remote_addr] = hits
    return len(hits) >= MAX_FAILS


def _record_fail():
    _fails.setdefault(request.remote_addr, []).append(time.time())


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not current_user():
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapper


# ------------------------------------------------------------------ guards
PUBLIC_API = {"/api/deployments"}   # POST also accepts a deploy token (checked in the view)


def install_guards(app):
    @app.before_request
    def guard():
        # Origin check for any state-changing request (defence in depth with SameSite cookies)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                return jsonify(error="cross-origin request blocked"), 403
        if request.path.startswith("/api/") and not current_user():
            token = app.config["DEPLOY_TOKEN"]
            if (request.path == "/api/deployments" and request.method == "POST" and token
                    and _eq(request.headers.get("X-Deploy-Token", ""), token)):
                return None
            return jsonify(error="authentication required"), 401
        return None

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "current_user": current_user()}

    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path in ("/login", "/signup", "/logout") or request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


# ------------------------------------------------------------------ routes
def _render(mode, error=None, status=200, username=""):
    return render_template("login.html", mode=mode, error=error, username=username,
                           signup_open=signup_open(), first_run=user_count() == 0,
                           next=request.values.get("next", "")), status


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if current_user():
            return redirect(safe_next(request.args.get("next")))
        return _render("login")
    if not _eq(request.form.get("csrf", ""), csrf_token()):
        return _render("login", "Your session expired. Please try again.", 400)
    if _rate_limited():
        return _render("login", "Too many failed attempts. Wait a few minutes and try again.", 429)
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    with db.session(current_app.config["DATABASE"]) as con:
        row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password) and row is not None
    if not ok:
        _record_fail()
        return _render("login", "Incorrect username or password.", 401, username)
    _fails.pop(request.remote_addr, None)
    session.clear()
    session["user"] = row["username"]
    session.permanent = bool(request.form.get("remember"))
    csrf_token()
    return redirect(safe_next(request.form.get("next")))


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if not signup_open():
        return _render("login", "Sign-up is closed. Ask an existing user, or set ALLOW_SIGNUP=1.", 403)
    if request.method == "GET":
        return _render("signup")
    if not _eq(request.form.get("csrf", ""), csrf_token()):
        return _render("signup", "Your session expired. Please try again.", 400)
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not USERNAME_RE.match(username):
        return _render("signup", "Username must be 3-32 characters: letters, numbers, dot, dash or underscore.", 400, username)
    if len(password) < 8:
        return _render("signup", "Password must be at least 8 characters.", 400, username)
    if password != request.form.get("confirm", ""):
        return _render("signup", "The two passwords do not match.", 400, username)
    try:
        create_user(current_app.config["DATABASE"], username, password)
    except Exception:  # unique constraint
        return _render("signup", "That username is taken.", 409, username)
    session.clear()
    session["user"] = username
    csrf_token()
    return redirect(url_for("dashboard"))


@bp.post("/logout")
def logout():
    if _eq(request.form.get("csrf", ""), session.get("csrf", "x")):
        session.clear()
    return redirect(url_for("home"))
