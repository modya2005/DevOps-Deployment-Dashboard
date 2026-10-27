"""SQLite access: schema plus small helpers. One short-lived connection per call keeps threads safe."""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS apps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  repo TEXT,                         -- "owner/name" for GitHub integration
  expected_status INTEGER NOT NULL DEFAULT 200,
  interval_s INTEGER NOT NULL DEFAULT 30,
  paused INTEGER NOT NULL DEFAULT 0, -- maintenance window: checks skipped and not counted
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
  ts REAL NOT NULL,
  ok INTEGER NOT NULL,
  status_code INTEGER,
  response_ms REAL,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_checks_app_ts ON checks(app_id, ts);
CREATE TABLE IF NOT EXISTS deployments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
  ts REAL NOT NULL,
  version TEXT NOT NULL,
  commit_sha TEXT NOT NULL,
  status TEXT NOT NULL,              -- success | failed
  note TEXT
);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  level TEXT NOT NULL,               -- INFO | WARN | ERROR
  app_id INTEGER,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
"""


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


@contextmanager
def session(path):
    con = connect(path)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db(path):
    with session(path) as con:
        con.executescript(SCHEMA)


def add_log(con, level, message, app_id=None, ts=None):
    con.execute(
        "INSERT INTO logs (ts, level, app_id, message) VALUES (?,?,?,?)",
        (ts or time.time(), level, app_id, message),
    )
