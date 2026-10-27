"""Small GitHub REST API client with a short in-memory cache (keeps within unauthenticated rate limits)."""
import re
import time

import requests

API = "https://api.github.com"
_cache = {}
TTL = 60

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(Exception):
    pass


def _get(path, token=None, params=None):
    key = (path, tuple(sorted((params or {}).items())), bool(token))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(API + path, headers=headers, params=params, timeout=8)
    except requests.exceptions.RequestException as e:
        raise GitHubError(f"could not reach GitHub: {e.__class__.__name__}")
    if r.status_code == 404:
        raise GitHubError("repository not found (private repos need GITHUB_TOKEN)")
    if r.status_code in (401, 403):
        left = r.headers.get("X-RateLimit-Remaining")
        raise GitHubError("GitHub rate limit reached, set GITHUB_TOKEN" if left == "0" else "GitHub denied the request, check GITHUB_TOKEN")
    if not r.ok:
        raise GitHubError(f"GitHub returned HTTP {r.status_code}")
    data = r.json()
    _cache[key] = (time.time(), data)
    return data


def repo_summary(repo, token=None):
    if not REPO_RE.match(repo or ""):
        raise GitHubError('repo must look like "owner/name"')
    info = _get(f"/repos/{repo}", token)
    commits = _get(f"/repos/{repo}/commits", token, {"per_page": 5})
    branches = _get(f"/repos/{repo}/branches", token, {"per_page": 10})
    try:
        runs = _get(f"/repos/{repo}/actions/runs", token, {"per_page": 5}).get("workflow_runs", [])
    except GitHubError:
        runs = []
    return {
        "full_name": info["full_name"],
        "description": info.get("description"),
        "url": info["html_url"],
        "default_branch": info["default_branch"],
        "stars": info.get("stargazers_count", 0),
        "open_issues": info.get("open_issues_count", 0),
        "pushed_at": info.get("pushed_at"),
        "commits": [{
            "sha": c["sha"][:7], "full_sha": c["sha"],
            "message": c["commit"]["message"].split("\n")[0],
            "author": (c["commit"]["author"] or {}).get("name"),
            "date": (c["commit"]["author"] or {}).get("date"),
            "url": c["html_url"],
        } for c in commits],
        "branches": [b["name"] for b in branches],
        "workflow_runs": [{
            "name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion"),
            "branch": r.get("head_branch"), "sha": (r.get("head_sha") or "")[:7],
            "url": r.get("html_url"), "date": r.get("created_at"),
        } for r in runs],
    }
