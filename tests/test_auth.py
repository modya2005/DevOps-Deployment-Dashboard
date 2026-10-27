import os
import tempfile
import unittest

import auth
from app import create_app


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.mktemp(suffix=".db")
        self.app = create_app({"TESTING": True, "DATABASE": self.f, "SCHEDULER": False, "ALLOW_SIGNUP": False})
        self.c = self.app.test_client()

    def tearDown(self):
        for s in ("", "-wal", "-shm"):
            if os.path.exists(self.f + s):
                os.remove(self.f + s)
        auth._fails.clear()

    def csrf(self, client=None):
        client = client or self.c
        client.get("/login")
        with client.session_transaction() as s:
            return s["csrf"]

    def login(self, user="alice", pw="supersecret1", client=None, **extra):
        client = client or self.c
        data = {"username": user, "password": pw, "csrf": self.csrf(client)}
        data.update(extra)
        return client.post("/login", data=data)

    def signup(self, user="alice", pw="supersecret1", confirm=None, client=None):
        client = client or self.c
        return client.post("/signup", data={"username": user, "password": pw, "confirm": pw if confirm is None else confirm, "csrf": self.csrf(client)})

    # --- public vs protected
    def test_home_and_login_are_public(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/login").status_code, 200)
        self.assertEqual(self.c.get("/health").status_code, 200)

    def test_dashboard_redirects_to_login_with_next(self):
        r = self.c.get("/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login?next=/dashboard", r.headers["Location"])

    def test_api_requires_auth(self):
        for path in ("/api/apps", "/api/overview", "/api/logs", "/api/deployments", "/api/environment"):
            self.assertEqual(self.c.get(path).status_code, 401, path)
        self.assertEqual(self.c.post("/api/apps", json={"name": "x", "url": "http://x.test"}).status_code, 401)
        self.assertEqual(self.c.post("/api/check").status_code, 401)

    # --- first-run setup and signup rules
    def test_first_account_then_signup_closes(self):
        self.assertIn(b"Create the owner account", self.c.get("/login").data)
        r = self.signup()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/dashboard").status_code, 200)   # signed in after setup
        other = self.app.test_client()
        self.assertEqual(other.get("/signup").status_code, 403)       # closed after the first user
        self.assertEqual(self.signup("mallory", client=other).status_code, 403)

    def test_signup_when_enabled(self):
        self.signup()
        self.app.config["ALLOW_SIGNUP"] = True
        other = self.app.test_client()
        self.assertEqual(self.signup("bob", client=other).status_code, 302)

    def test_signup_validation(self):
        self.assertEqual(self.signup("a").status_code, 400)                       # too short
        self.assertEqual(self.signup("bad name!").status_code, 400)               # characters
        self.assertEqual(self.signup("alice", "short").status_code, 400)          # password length
        self.assertEqual(self.signup("alice", "supersecret1", "different1").status_code, 400)
        self.signup()
        self.app.config["ALLOW_SIGNUP"] = True
        self.assertEqual(self.signup("ALICE", client=self.app.test_client()).status_code, 409)  # case-insensitive unique

    # --- login behaviour
    def test_login_success_and_logout(self):
        self.signup()
        self.c.post("/logout", data={"csrf": self.csrf()})
        self.assertEqual(self.c.get("/api/apps").status_code, 401)
        r = self.login(next="/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/dashboard"))
        self.assertEqual(self.c.get("/api/apps").status_code, 200)
        with self.c.session_transaction() as s:
            tok = s["csrf"]
        self.c.post("/logout", data={"csrf": tok})
        self.assertEqual(self.c.get("/api/apps").status_code, 401)

    def test_logout_needs_csrf(self):
        self.signup()
        self.c.post("/logout", data={"csrf": "wrong"})
        self.assertEqual(self.c.get("/api/apps").status_code, 200)   # still signed in

    def test_wrong_password_and_unknown_user_same_message(self):
        self.signup()
        a = self.login("alice", "wrong-password", client=self.app.test_client())
        b = self.login("nobody", "wrong-password", client=self.app.test_client())
        self.assertEqual(a.status_code, 401)
        self.assertEqual(b.status_code, 401)
        self.assertIn(b"Incorrect username or password", a.data)
        self.assertIn(b"Incorrect username or password", b.data)

    def test_csrf_required_on_login(self):
        self.signup()
        c2 = self.app.test_client()
        c2.get("/login")
        r = c2.post("/login", data={"username": "alice", "password": "supersecret1", "csrf": "forged"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(c2.get("/api/apps").status_code, 401)

    def test_open_redirect_blocked(self):
        self.signup()
        for bad in ("//evil.test", "https://evil.test", "/\\evil.test"):
            c2 = self.app.test_client()
            r = self.login(client=c2, next=bad)
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r.headers["Location"].endswith("/dashboard"), bad)

    def test_rate_limit_after_repeated_failures(self):
        self.signup()
        c2 = self.app.test_client()
        codes = [self.login("alice", "nope-nope-1", client=c2).status_code for _ in range(auth.MAX_FAILS + 1)]
        self.assertEqual(codes[:auth.MAX_FAILS], [401] * auth.MAX_FAILS)
        self.assertEqual(codes[-1], 429)
        self.assertEqual(self.login("alice", "supersecret1", client=c2).status_code, 429)  # even the right one is blocked

    def test_non_ascii_credentials_do_not_crash(self):
        self.signup()
        r = self.login("alicé", "pässwörd-1", client=self.app.test_client())
        self.assertEqual(r.status_code, 401)

    def test_cross_origin_write_blocked(self):
        self.signup()
        r = self.c.post("/api/apps", json={"name": "x", "url": "http://x.test"}, headers={"Origin": "https://evil.test"})
        self.assertEqual(r.status_code, 403)

    def test_password_is_hashed_and_cookie_flags(self):
        self.signup()
        import db
        with db.session(self.f) as con:
            h = con.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertNotIn("supersecret1", h)
        self.assertTrue(h.startswith(("pbkdf2:", "scrypt:")))
        r = self.login(client=self.app.test_client())
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_admin_seed_from_env(self):
        f = tempfile.mktemp(suffix=".db")
        app = create_app({"TESTING": True, "DATABASE": f, "SCHEDULER": False, "ADMIN_USER": "root1", "ADMIN_PASSWORD": "longpassword1"})
        c = app.test_client()
        c.get("/login")
        with c.session_transaction() as s:
            tok = s["csrf"]
        r = c.post("/login", data={"username": "root1", "password": "longpassword1", "csrf": tok})
        self.assertEqual(r.status_code, 302)
        os.remove(f)


class FaqTests(unittest.TestCase):
    def test_home_renders_every_faq_with_working_anchors(self):
        import faq
        f = tempfile.mktemp(suffix=".db")
        app = create_app({"TESTING": True, "DATABASE": f, "SCHEDULER": False})
        html = app.test_client().get("/").get_data(as_text=True)
        ids = [x["id"] for x in faq.FAQS]
        self.assertEqual(len(ids), len(set(ids)))
        for x in faq.FAQS:
            self.assertIn(f'id="faq-{x["id"]}"', html)
            self.assertIn(f'aria-controls="a-{x["id"]}"', html)
            self.assertIn(f'id="a-{x["id"]}"', html)
            self.assertIn(x["cat"], faq.CATEGORIES)
        self.assertIn("faq-forgot-password", html + open("templates/login.html").read())  # login links to a real FAQ id
        self.assertIn("forgot-password", ids)
        os.remove(f)


if __name__ == "__main__":
    unittest.main()
