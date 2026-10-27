#!/usr/bin/env python3
"""Reset a user's password (or create the user). Run on the machine that hosts the database.

  python scripts/reset_password.py alice
"""
import getpass
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from werkzeug.security import generate_password_hash  # noqa: E402

import db  # noqa: E402
from auth import USERNAME_RE  # noqa: E402
from config import Config  # noqa: E402

if len(sys.argv) != 2 or not USERNAME_RE.match(sys.argv[1]):
    sys.exit("usage: reset_password.py USERNAME   (3-32 chars: letters, numbers, . _ -)")
user = sys.argv[1]
pw = getpass.getpass("New password (min 8 characters): ")
if len(pw) < 8 or pw != getpass.getpass("Repeat password: "):
    sys.exit("Passwords must match and be at least 8 characters.")
db.init_db(Config.DATABASE)
with db.session(Config.DATABASE) as con:
    cur = con.execute("UPDATE users SET password_hash=? WHERE username=?", (generate_password_hash(pw), user))
    if cur.rowcount == 0:
        con.execute("INSERT INTO users (username,password_hash,created_at) VALUES (?,?,?)",
                    (user, generate_password_hash(pw), time.time()))
        print(f"Created user {user}.")
    else:
        print(f"Password updated for {user}.")
