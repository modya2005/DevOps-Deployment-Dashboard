#!/usr/bin/env python3
"""Record a deployment on the dashboard. Used by CI/CD after a deploy step.

  python scripts/record_deploy.py --url http://localhost:5000 --app "Job Tracker" \
      --version v1.4.2 --sha "$GITHUB_SHA" --status success

Set DEPLOY_TOKEN in the environment if the dashboard requires it.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

p = argparse.ArgumentParser()
p.add_argument("--url", default=os.environ.get("DASHBOARD_URL", "http://localhost:5000"))
p.add_argument("--app", required=True, help="application name as registered in the dashboard")
p.add_argument("--version", required=True)
p.add_argument("--sha", required=True, help="Git commit SHA being deployed")
p.add_argument("--status", choices=["success", "failed"], default="success")
p.add_argument("--note", default="")
a = p.parse_args()

headers = {"Content-Type": "application/json"}
if os.environ.get("DEPLOY_TOKEN"):
    headers["X-Deploy-Token"] = os.environ["DEPLOY_TOKEN"]
req = urllib.request.Request(
    a.url.rstrip("/") + "/api/deployments", method="POST", headers=headers,
    data=json.dumps({"app": a.app, "version": a.version, "commit_sha": a.sha, "status": a.status, "note": a.note}).encode())
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print("recorded:", r.status)
except urllib.error.HTTPError as e:
    print("dashboard rejected the deployment:", e.read().decode(), file=sys.stderr)
    sys.exit(1)
except urllib.error.URLError as e:
    print("could not reach dashboard:", e.reason, file=sys.stderr)
    sys.exit(1)
