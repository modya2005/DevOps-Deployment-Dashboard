import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import db
from monitor import health_checks as hc


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            time.sleep(1.5)
        code = 500 if self.path == "/err" else 200
        try:
            self.send_response(code)
            self.end_headers()
            self.wfile.write(b"x")
        except (BrokenPipeError, ConnectionResetError):
            pass  # client gave up (timeout test)

    def log_message(self, *a):
        pass


class CheckUrlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), H)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_success(self):
        r = hc.check_url(self.base + "/ok", 200, 3)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status_code"], 200)
        self.assertGreaterEqual(r["response_ms"], 0)

    def test_wrong_status_is_failure(self):
        r = hc.check_url(self.base + "/err", 200, 3)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status_code"], 500)
        self.assertIn("expected HTTP 200", r["error"])

    def test_expected_status_can_be_configured(self):
        self.assertTrue(hc.check_url(self.base + "/err", 500, 3)["ok"])

    def test_timeout_handled(self):
        r = hc.check_url(self.base + "/slow", 200, 0.3)
        self.assertFalse(r["ok"])
        self.assertIsNone(r["response_ms"])
        self.assertIn("timeout", r["error"])

    def test_unreachable_handled(self):
        r = hc.check_url("http://127.0.0.1:1/", 200, 2)
        self.assertFalse(r["ok"])
        self.assertIn("connection failed", r["error"])


class StatusTests(unittest.TestCase):
    def c(self, ok, ms=100):
        return {"ok": ok, "response_ms": ms if ok else None}

    def test_unknown_and_paused(self):
        self.assertEqual(hc.derive_status([]), "unknown")
        self.assertEqual(hc.derive_status([self.c(True)], paused=True), "paused")

    def test_healthy_slow(self):
        self.assertEqual(hc.derive_status([self.c(True, 100)]), "healthy")
        self.assertEqual(hc.derive_status([self.c(True, 900)], slow_ms=500), "slow")

    def test_degraded_then_down(self):
        self.assertEqual(hc.derive_status([self.c(False), self.c(True)]), "degraded")
        self.assertEqual(hc.derive_status([self.c(False), self.c(False)]), "down")


class UptimeTests(unittest.TestCase):
    def test_uptime_formula_and_window(self):
        f = tempfile.mktemp(suffix=".db")
        db.init_db(f)
        now = time.time()
        with db.session(f) as con:
            con.execute("INSERT INTO apps (name,url,created_at) VALUES ('a','http://x',?)", (now,))
            for i, ok in enumerate([1, 1, 1, 0]):          # inside the window: 3 of 4 pass
                con.execute("INSERT INTO checks (app_id,ts,ok) VALUES (1,?,?)", (now - 60 * i, ok))
            con.execute("INSERT INTO checks (app_id,ts,ok) VALUES (1,?,0)", (now - 7200,))  # outside 1h
            self.assertEqual(hc.uptime_pct(con, 1, 3600), 75.0)
            self.assertAlmostEqual(hc.uptime_pct(con, 1, 86400), 60.0)
            con.execute("INSERT INTO apps (name,url,created_at) VALUES ('b','http://y',?)", (now,))
            self.assertIsNone(hc.uptime_pct(con, 2, 3600))  # no data is not 100%
        os.remove(f)


if __name__ == "__main__":
    unittest.main()
