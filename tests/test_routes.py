import os
import tempfile
import time
import unittest
from unittest import mock

import auth
import db
from app import create_app
from monitor import github_client


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.mktemp(suffix=".db")
        self.app = create_app({"TESTING": True, "DATABASE": self.f, "DEPLOY_TOKEN": "", "SCHEDULER": False})
        self.c = self.app.test_client()
        auth.create_user(self.f, "tester", "correct-horse-1")
        self.c.post("/login", data={"username": "tester", "password": "correct-horse-1", "csrf": self.csrf(self.c)})

    @staticmethod
    def csrf(client):
        client.get("/login")
        with client.session_transaction() as s:
            return s["csrf"]

    def tearDown(self):
        for s in ("", "-wal", "-shm"):
            if os.path.exists(self.f + s):
                os.remove(self.f + s)

    def add_app(self, **kw):
        body = {"name": "Demo", "url": "http://127.0.0.1:9/health"}
        body.update(kw)
        return self.c.post("/api/apps", json=body)

    def test_pages(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        self.assertEqual(self.c.get("/dashboard").status_code, 200)
        r = self.c.get("/health")
        self.assertEqual(r.get_json()["status"], "ok")

    def test_add_validates_input(self):
        self.assertEqual(self.add_app(name="").status_code, 400)
        self.assertEqual(self.add_app(url="ftp://x").status_code, 400)
        self.assertEqual(self.add_app(repo="not a repo").status_code, 400)
        self.assertEqual(self.add_app(interval_s=1).status_code, 400)
        self.assertEqual(self.add_app().status_code, 201)

    def test_status_is_derived_from_checks_not_stored(self):
        self.add_app()
        time.sleep(0.3)  # allow the background first check to finish
        with db.session(self.f) as con:
            con.execute("DELETE FROM checks")
        apps = self.c.get("/api/apps").get_json()
        self.assertEqual(apps[0]["status"], "unknown")
        self.c.post("/api/check")           # nothing listens on port 9, so this fails
        apps = self.c.get("/api/apps").get_json()
        self.assertEqual(apps[0]["status"], "degraded")
        self.assertEqual(apps[0]["uptime"]["1h"], 0.0)
        self.c.post("/api/check")
        self.assertEqual(self.c.get("/api/apps").get_json()[0]["status"], "down")

    def test_failed_check_writes_error_log_and_search_works(self):
        self.add_app()
        self.c.post("/api/check")
        logs = self.c.get("/api/logs?level=ERROR").get_json()
        self.assertTrue(any("health check failed" in l["message"] for l in logs))
        self.assertTrue(self.c.get("/api/logs?q=health+check").get_json())
        self.assertEqual(self.c.get("/api/logs?q=zzz-no-match").get_json(), [])
        self.assertEqual(self.c.get("/api/logs?q=%25").get_json(), [])  # % is literal, not a wildcard

    def test_pause_skips_checks(self):
        r = self.add_app()
        app_id = r.get_json()["id"]
        time.sleep(0.3)
        self.c.patch(f"/api/apps/{app_id}", json={"paused": True})
        with db.session(self.f) as con:
            con.execute("DELETE FROM checks")
        self.assertEqual(self.c.post("/api/check").get_json()["checked"], 0)
        self.assertEqual(self.c.get("/api/apps").get_json()[0]["status"], "paused")

    def test_deployments_require_valid_sha_and_link_to_commit(self):
        app_id = self.add_app().get_json()["id"]
        bad = self.c.post("/api/deployments", json={"app_id": app_id, "version": "v1", "commit_sha": "zzz"})
        self.assertEqual(bad.status_code, 400)
        ok = self.c.post("/api/deployments", json={"app_id": app_id, "version": "v1.0.0", "commit_sha": "A3F91C2", "status": "success"})
        self.assertEqual(ok.status_code, 201)
        d = self.c.get("/api/deployments").get_json()
        self.assertEqual(d[0]["commit_sha"], "a3f91c2")
        self.assertEqual(d[0]["app_name"], "Demo")
        self.assertTrue(any("v1.0.0" in l["message"] for l in self.c.get("/api/logs").get_json()))

    def test_deploy_token_enforced(self):
        self.app.config["DEPLOY_TOKEN"] = "s3cret"
        app_id = self.add_app().get_json()["id"]
        body = {"app_id": app_id, "version": "v1", "commit_sha": "abcdef1"}
        anon = self.app.test_client()  # CI has no session, only the token
        self.assertEqual(anon.post("/api/deployments", json=body).status_code, 401)
        self.assertEqual(anon.post("/api/deployments", json=body, headers={"X-Deploy-Token": "nope"}).status_code, 401)
        self.assertEqual(anon.post("/api/deployments", json=body, headers={"X-Deploy-Token": "s3cret"}).status_code, 201)
        self.assertEqual(anon.post("/api/deployments", json=body, headers={"X-Deploy-Token": "sécret"}).status_code, 401)  # non-ASCII must not crash

    def test_delete_cascades(self):
        app_id = self.add_app().get_json()["id"]
        self.c.post("/api/check")
        self.c.delete(f"/api/apps/{app_id}")
        with db.session(self.f) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM checks").fetchone()["n"], 0)
        self.assertEqual(self.c.delete(f"/api/apps/{app_id}").status_code, 404)

    def test_environment_reports_host_metrics(self):
        e = self.c.get("/api/environment").get_json()
        self.assertIn("cpu_percent", e)
        self.assertIn("python", e)
        self.assertEqual(e["database"], "SQLite")

    def test_github_without_repo_and_with_mocked_api(self):
        app_id = self.add_app().get_json()["id"]
        self.assertEqual(self.c.get(f"/api/apps/{app_id}/github").status_code, 404)
        app2 = self.add_app(name="Repo app", repo="octo/demo").get_json()["id"]
        fake = {
            "/repos/octo/demo": {"full_name": "octo/demo", "html_url": "https://github.com/octo/demo", "default_branch": "main", "stargazers_count": 1, "open_issues_count": 0, "pushed_at": None},
            "/repos/octo/demo/commits": [{"sha": "a" * 40, "html_url": "u", "commit": {"message": "Init\n\nbody", "author": {"name": "Ann", "date": "2026-01-01T00:00:00Z"}}}],
            "/repos/octo/demo/branches": [{"name": "main"}],
            "/repos/octo/demo/actions/runs": {"workflow_runs": [{"name": "tests", "status": "completed", "conclusion": "success", "head_branch": "main", "head_sha": "a" * 40, "html_url": "u", "created_at": None}]},
        }
        with mock.patch.object(github_client, "_get", side_effect=lambda path, token=None, params=None: fake[path]):
            r = self.c.get(f"/api/apps/{app2}/github").get_json()
        self.assertEqual(r["commits"][0]["message"], "Init")
        self.assertEqual(r["workflow_runs"][0]["conclusion"], "success")

    def test_github_error_is_reported(self):
        app2 = self.add_app(name="Repo app", repo="octo/demo").get_json()["id"]
        with mock.patch.object(github_client, "_get", side_effect=github_client.GitHubError("rate limit")):
            r = self.c.get(f"/api/apps/{app2}/github")
        self.assertEqual(r.status_code, 502)
        self.assertIn("rate limit", r.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
