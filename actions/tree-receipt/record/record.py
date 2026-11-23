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
import urllib.request

REQUEST_TIMEOUT = 20
TREE = re.compile(r"[0-9a-f]{40}")
WORKFLOW_REF = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(\.github/workflows/[A-Za-z0-9_./-]+)@\S+")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class Skip(Exception):
    """Raised for anything that means no receipt is written."""


def workflow_path(ref):
    match = WORKFLOW_REF.fullmatch(ref or "")
    if not match:
        raise Skip("workflow-ref")
    return match.group(1)


def read_event(env):
    try:
        with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
            data = json.load(handle)
    except (KeyError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def target_sha(env):
    """Return the commit the workflow ran for, or raise Skip for fork PRs."""
    name = env.get("TREE_RECEIPT_EVENT_NAME", "")
    if name in ("pull_request", "pull_request_target"):
        pr = read_event(env).get("pull_request")
        if not isinstance(pr, dict):
            raise Skip("event")
        head, base = pr.get("head"), pr.get("base")
        try:
            head_repo = head["repo"]["full_name"]
            base_repo = base["repo"]["full_name"]
            sha = head["sha"]
        except (KeyError, TypeError):
            raise Skip("event")
        if head_repo != base_repo:
            raise Skip("fork")
    else:
        sha = env.get("GITHUB_SHA", "")
    if not isinstance(sha, str) or not TREE.fullmatch(sha):
        raise Skip("sha")
    return sha


def default_git(args):
    return subprocess.run(["git"] + args, capture_output=True, text=True,
                          timeout=30, check=True).stdout.strip()


def tested_tree(env, git):
    tree = (env.get("TREE_RECEIPT_TESTED_TREE") or "").strip()
    if not tree:
        try:
            tree = git(["rev-parse", "HEAD^{tree}"])
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
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
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
    tree = tested_tree(env, git)
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
