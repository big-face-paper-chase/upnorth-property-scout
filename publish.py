#!/usr/bin/env python3
"""Publish the scout's site files to GitHub Pages via the GitHub API.

Usage:
    python3 publish.py --all         # create repo (if needed), push the site files, enable Pages
    python3 publish.py --data-only   # push only data.json (daily cron)
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response, read_response_body

HERE = Path(__file__).parent
API = "https://api.github.com"
HOSTS = ["api.github.com"]
CRED = "custom.github"
SITE_FILES = ("index.html", "app.js", "styles.css", "data.json")


def _owner_repo():
    """OWNER/REPO from GITHUB_REPOSITORY (owner/repo), else the original repo."""
    gr = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in gr:
        return gr.split("/", 1)
    return "big-face-paper-chase", "upnorth-property-scout"


OWNER, REPO = _owner_repo()
GH_API = str(HERE.parent / "skills" / "github" / "bin" / "github-api")


def api(method, path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(API + path, data=body, method=method,
                                 headers={"User-Agent": "upnorth-scout-publish",
                                          "Accept": "application/vnd.github+json"})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    add_surrogate_to_request(req, CRED, allowed_hosts=HOSTS)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.status, read_json_response(resp)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(read_response_body(e).decode())
        except Exception:
            payload = {"message": f"HTTP {e.code}"}
        return e.code, payload


def ensure_repo():
    status, payload = api("GET", f"/repos/{OWNER}/{REPO}")
    if status == 200:
        print("repo exists:", payload["html_url"])
        return
    print("creating repo...")
    r = subprocess.run([GH_API, "create-repo", REPO], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        sys.exit(1)


def put_file(local: Path, repo_path: str, message: str):
    r = subprocess.run(
        [GH_API, "put-file", OWNER, REPO, repo_path, str(local), "-m", message],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"FAILED {repo_path}: {r.stderr.strip()}", file=sys.stderr)
        return False
    print("pushed", repo_path)
    return True


def enable_pages():
    status, payload = api("GET", f"/repos/{OWNER}/{REPO}/pages")
    if status == 200:
        print("pages already enabled:", payload.get("html_url"))
        return
    status, payload = api("POST", f"/repos/{OWNER}/{REPO}/pages",
                          {"source": {"branch": "main", "path": "/"}})
    if status in (200, 201):
        print("pages enabled:", payload.get("html_url"))
    else:
        print("pages enable failed:", payload, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--data-only", action="store_true")
    args = ap.parse_args()

    ensure_repo()
    if args.data_only:
        files = [HERE / "data.json"]
    else:
        files = [HERE / name for name in SITE_FILES]
    missing = [f for f in files if not f.is_file()]
    if missing:
        print("missing site file(s):", ", ".join(f.name for f in missing), file=sys.stderr)
        sys.exit(1)
    ok = True
    for f in files:
        rel = f.relative_to(HERE).as_posix()
        ok &= put_file(f, rel, f"site update: {rel}")
    if args.all:
        enable_pages()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
