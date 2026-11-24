#!/usr/bin/env python3
"""Write a tree receipt: a commit status naming the tree this run tested.

Everything comes from environment variables. A failure to write is logged and
exits 0, because a missing receipt only costs a full run later.
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REQUEST_TIMEOUT = 20
TREE = re.compile(r"[0-9a-f]{40}")
WORKFLOW_REF = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(\.github/workflows/[A-Za-z0-9_./-]+)@\S+")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class Skip(Exception):
    """Raised for anything that means no receipt is written."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the Authorization header never follows one."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def workflow_path(ref):
    match = WORKFLOW_REF.fullmatch(ref or "")
    if not match:
        raise Skip("workflow-ref")
    return match.group(1)


def target_sha(env):
    """Return the pushed commit. Receipts exist only for push events."""
    name = env.get("TREE_RECEIPT_EVENT_NAME", "")
    if name != "push":
        raise Skip("event {0}".format(name if re.fullmatch(r"[a-z_]{1,40}", name) else "?"))
    sha = env.get("GITHUB_SHA", "")
    if not isinstance(sha, str) or not TREE.fullmatch(sha):
        raise Skip("sha")
    return sha


def default_git(args):
    return subprocess.run(["git"] + args, capture_output=True, text=True,
                          timeout=30, check=True).stdout.strip()


def tested_tree(sha, git):
    try:
        tree = git(["rev-parse", sha + "^{tree}"])
    except Exception:
        raise Skip("no-tree")
    if not TREE.fullmatch(tree):
        raise Skip("invalid-tree")
    return tree


def post_status(url, auth, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + auth,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codeswhat-tree-receipt",
        },
    )
    with OPENER.open(request, timeout=REQUEST_TIMEOUT) as response:
        if response.status != 201:
            raise OSError("status")


def record(env, git=default_git, post=post_status):
    """Return a log line. Raises Skip for deliberate non-writes."""
    repo = env.get("GITHUB_REPOSITORY", "")
    if not REPO.fullmatch(repo) or any(p in (".", "..") for p in repo.split("/")):
        raise Skip("repository")
    auth = env.get("TREE_RECEIPT_AUTH", "")
    if not auth:
        raise Skip("no-auth")
    path = workflow_path(env.get("GITHUB_WORKFLOW_REF"))
    sha = target_sha(env)
    tree = tested_tree(sha, git)
    run_id = env.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"[0-9]+", run_id):
        raise Skip("run-id")
    server = (env.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    api = (env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    payload = {
        "state": "success",
        "target_url": "{0}/{1}/actions/runs/{2}".format(server, repo, run_id),
        "description": "tree=" + tree,
        "context": "tree-receipt/" + path,
    }
    post("{0}/repos/{1}/statuses/{2}".format(api, repo, sha), auth, payload)
    return "recorded commit={0} tree={1} run={2}".format(sha, tree, run_id)


def main(env=None, git=default_git, post=post_status):
    env = os.environ if env is None else env
    try:
        line = record(env, git, post)
    except Skip as skip:
        line = "skipped ({0})".format(skip)
    except Exception:
        line = "write failed, no receipt recorded"
    print("tree-receipt record: " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
