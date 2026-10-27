"""Configuration loaded from environment variables (and an optional .env file)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv():
    env = BASE_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.split("#", 1)[0].strip() if not line.strip().startswith("#") else ""
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Config:
    ENV_NAME = os.environ.get("ENV_NAME", "local")
    APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
    DATABASE = os.environ.get("DATABASE", str(BASE_DIR / "instance" / "dashboard.db"))
    CHECK_TIMEOUT = _int("CHECK_TIMEOUT", 5)
    SLOW_MS = _int("SLOW_MS", 500)
    DEFAULT_INTERVAL = _int("DEFAULT_INTERVAL", 30)
    RETENTION_DAYS = _int("RETENTION_DAYS", 30)
    SCHEDULER = os.environ.get("SCHEDULER", "1") == "1"
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
    DEPLOY_TOKEN = os.environ.get("DEPLOY_TOKEN", "")
    SEED_DEMO = os.environ.get("SEED_DEMO", "1") == "1"
    PORT = _int("PORT", 5000)
    # Auth
    SECRET_KEY = os.environ.get("SECRET_KEY", "")          # generated and stored in instance/ if empty
    ALLOW_SIGNUP = os.environ.get("ALLOW_SIGNUP", "0") == "1"  # first account can always be created
    ADMIN_USER = os.environ.get("ADMIN_USER", "")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
    SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "0") == "1"  # set 1 behind HTTPS
