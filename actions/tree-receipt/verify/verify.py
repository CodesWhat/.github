#!/usr/bin/env python3
"""Decide whether an earlier run already verified this exact tree.

Everything comes from environment variables. The log carries only SHAs, counts,
run ids and the decision, and GITHUB_OUTPUT gets reuse=true or reuse=false.
Any doubt resolves to false. Statuses are lookup hints only: a cited run counts
when it is a push run whose commit tree and workflow blob are independently
confirmed and whose jobs all succeeded.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REQUEST_TIMEOUT = 20
DEADLINE_SECONDS = 120
PER_PAGE = 100
STATUS_PAGES = 3
JOB_PAGES = 5
MAX_CANDIDATES = 10
MAX_RUN_CHECKS = 5
BOT = "github-actions[bot]"
HEX40 = re.compile(r"[0-9a-f]{40}")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
WORKFLOW_REF = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(\.github/workflows/[A-Za-z0-9_./-]+)@\S+")


class Doubt(Exception):
    """Raised for anything that should resolve to reuse=false."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the Authorization header never follows one."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def workflow_path(ref):
    match = WORKFLOW_REF.fullmatch(ref or "")
    if not match:
        raise Doubt("workflow-ref")
    return match.group(1)


def default_git(args):
    return subprocess.run(["git"] + args, capture_output=True, text=True,
                          timeout=30, check=True).stdout.strip()


def fetch_json(url, auth, timeout=REQUEST_TIMEOUT):
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + auth,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codeswhat-tree-receipt",
        },
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise Doubt("status")
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if 300 <= error.code < 400:
            raise Doubt("redirect")
        raise


def read_event(env):
    try:
        with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
            data = json.load(handle)
    except (KeyError, OSError, ValueError):
        raise Doubt("event")
    if not isinstance(data, dict):
        raise Doubt("event")
    return data


def current_tree(git):
    try:
        tree = git(["rev-parse", "HEAD^{tree}"])
    except Exception:
        raise Doubt("no-git")
    if not HEX40.fullmatch(tree):
        raise Doubt("tree")
    return tree


def candidates(env, git, api, repo, auth, fetch, log):
    name = env.get("TREE_RECEIPT_EVENT_NAME", "")
    found = []
    if name == "pull_request":
        try:
            found.append(read_event(env)["pull_request"]["head"]["sha"])
        except (KeyError, TypeError):
            raise Doubt("event")
    elif name == "push":
        try:
            head = git(["rev-parse", "HEAD"])
            parents = git(["rev-parse", "HEAD^@"]).split()
        except Exception:
            raise Doubt("no-git")
        if not HEX40.fullmatch(head):
            raise Doubt("head")
        if not parents:
            try:
                shallow = git(["rev-parse", "--is-shallow-repository"]) == "true"
            except Exception:
                shallow = False
            if shallow:
                log.append("shallow checkout, parents unavailable")
        found.extend(parents)
        pulls = fetch("{0}/repos/{1}/commits/{2}/pulls?per_page={3}".format(
            api, repo, head, PER_PAGE), auth)
        if not isinstance(pulls, list):
            raise Doubt("shape")
        for pull in pulls:
            try:
                found.append(pull["head"]["sha"])
            except (KeyError, TypeError):
                raise Doubt("shape")
    else:
        raise Doubt("event-name")
    unique = []
    for sha in found:
        if not isinstance(sha, str) or not HEX40.fullmatch(sha):
            raise Doubt("sha")
        if sha not in unique:
            unique.append(sha)
    return unique[:MAX_CANDIDATES]


def statuses(api, repo, sha, auth, fetch):
    out = []
    for page in range(1, STATUS_PAGES + 1):
        data = fetch("{0}/repos/{1}/commits/{2}/statuses?per_page={3}&page={4}".format(
            api, repo, sha, PER_PAGE, page), auth)
        if not isinstance(data, list):
            raise Doubt("shape")
        out.extend(data)
        if len(data) < PER_PAGE:
            break
    return out


def parse_time(text):
    if not isinstance(text, str):
        raise Doubt("time")
    try:
        parsed = datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise Doubt("time")
    return parsed.replace(tzinfo=datetime.timezone.utc)


def commit_tree(api, repo, sha, auth, fetch):
    data = fetch("{0}/repos/{1}/git/commits/{2}".format(api, repo, sha), auth)
    tree = data.get("tree") if isinstance(data, dict) else None
    value = tree.get("sha") if isinstance(tree, dict) else None
    if not isinstance(value, str) or not HEX40.fullmatch(value):
        raise Doubt("shape")
    return value


def file_blob(api, repo, path, ref, auth, fetch):
    data = fetch("{0}/repos/{1}/contents/{2}?ref={3}".format(
        api, repo, urllib.parse.quote(path, safe="/"), ref), auth)
    blob = data.get("sha") if isinstance(data, dict) else None
    if not isinstance(blob, str) or not HEX40.fullmatch(blob):
        raise Doubt("shape")
    return blob


def default_branch_blob(api, repo, path, auth, fetch):
    """Blob of the workflow file at the default branch's current head sha."""
    info = fetch("{0}/repos/{1}".format(api, repo), auth)
    branch = info.get("default_branch") if isinstance(info, dict) else None
    if not isinstance(branch, str) or not branch:
        raise Doubt("shape")
    head = fetch("{0}/repos/{1}/branches/{2}".format(
        api, repo, urllib.parse.quote(branch, safe="")), auth)
    commit = head.get("commit") if isinstance(head, dict) else None
    sha = commit.get("sha") if isinstance(commit, dict) else None
    if not isinstance(sha, str) or not HEX40.fullmatch(sha):
        raise Doubt("shape")
    return file_blob(api, repo, path, sha, auth, fetch)


def validate_run(api, repo, run_id, sha, path, tree, claimed, trusted, max_age, now,
                 auth, fetch):
    """Return the run's created_at, or None when the run is not good evidence."""
    run = fetch("{0}/repos/{1}/actions/runs/{2}".format(api, repo, run_id), auth)
    if not isinstance(run, dict):
        raise Doubt("shape")
    full = (run.get("repository") or {}).get("full_name") if isinstance(
        run.get("repository"), dict) else None
    run_path = run.get("path")
    if not isinstance(run_path, str):
        raise Doubt("shape")
    if (full != repo or run_path.split("@", 1)[0] != path
            or run.get("event") != "push"
            or run.get("conclusion") != "success" or run.get("status") != "completed"
            or run.get("head_sha") != sha):
        return None
    created = parse_time(run.get("created_at"))
    age = now - created
    if age < datetime.timedelta(0) or age > datetime.timedelta(hours=max_age):
        return None
    run_tree = commit_tree(api, repo, sha, auth, fetch)
    if run_tree != tree or run_tree != claimed:
        return None
    if file_blob(api, repo, path, sha, auth, fetch) != trusted():
        return None
    seen = 0
    total = None
    for page in range(1, JOB_PAGES + 1):
        data = fetch("{0}/repos/{1}/actions/runs/{2}/jobs?per_page={3}&page={4}".format(
            api, repo, run_id, PER_PAGE, page), auth)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        count = data.get("total_count") if isinstance(data, dict) else None
        if not isinstance(jobs, list) or isinstance(count, bool) or not isinstance(count, int):
            raise Doubt("shape")
        total = count
        for job in jobs:
            if not isinstance(job, dict):
                raise Doubt("shape")
            if job.get("conclusion") != "success":
                return None
        seen += len(jobs)
        if seen >= total or len(jobs) < PER_PAGE:
            break
    if total is None or total == 0 or seen != total:
        raise Doubt("jobs-incomplete")
    return created


def decide(env, git=default_git, fetch=fetch_json, now=None):
    """Return (reuse, evidence_url, log_lines)."""
    log = []
    repo = env.get("GITHUB_REPOSITORY", "")
    if not REPO.fullmatch(repo) or any(p in (".", "..") for p in repo.split("/")):
        raise Doubt("repository")
    auth = env.get("TREE_RECEIPT_AUTH", "")
    if not auth:
        raise Doubt("no-auth")
    try:
        max_age = float(env.get("TREE_RECEIPT_MAX_AGE_HOURS", "168"))
    except ValueError:
        raise Doubt("max-age")
    if not 0 < max_age < 24 * 365:
        raise Doubt("max-age")
    path = workflow_path(env.get("GITHUB_WORKFLOW_REF"))
    api = (env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    server = (env.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    started = time.monotonic()
    tree = current_tree(git)

    def budgeted(url, token):
        remaining = DEADLINE_SECONDS - (time.monotonic() - started)
        if remaining <= 0:
            raise Doubt("deadline")
        return fetch(url, token, timeout=min(REQUEST_TIMEOUT, remaining))

    cands = candidates(env, git, api, repo, auth, budgeted, log)
    cache = []

    def trusted():
        if not cache:
            cache.append(default_branch_blob(api, repo, path, auth, budgeted))
        return cache[0]

    log.append("tree={0} candidates={1}".format(tree, len(cands)))
    context = "tree-receipt/" + path
    description = "tree=" + tree
    url_re = re.compile(r"{0}/{1}/actions/runs/([0-9]+)".format(
        re.escape(server), re.escape(repo)))
    checked = set()
    for sha in cands:
        if time.monotonic() - started > DEADLINE_SECONDS:
            raise Doubt("deadline")
        for status in statuses(api, repo, sha, auth, budgeted):
            if not isinstance(status, dict):
                raise Doubt("shape")
            creator = status.get("creator")
            login = creator.get("login") if isinstance(creator, dict) else None
            target = status.get("target_url")
            match = url_re.fullmatch(target) if isinstance(target, str) else None
            if (status.get("context") != context or status.get("state") != "success"
                    or login != BOT or status.get("description") != description
                    or not match):
                continue
            run_id = match.group(1)
            if run_id in checked:
                continue
            if len(checked) >= MAX_RUN_CHECKS:
                log.append("run check limit reached")
                return False, "", log
            checked.add(run_id)
            created = validate_run(api, repo, run_id, sha, path, tree, tree, trusted,
                                   max_age, now, auth, budgeted)
            if created is None:
                log.append("rejected candidate={0} run={1}".format(sha, run_id))
                continue
            if time.monotonic() - started > DEADLINE_SECONDS:
                raise Doubt("deadline")
            evidence = target
            log.append("evidence candidate={0} run={1} created_at={2}".format(
                sha, evidence, created.strftime("%Y-%m-%dT%H:%M:%SZ")))
            return True, evidence, log
    return False, "", log


def write_output(path, reuse, evidence):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("reuse={0}\nevidence-run={1}\n".format(
            "true" if reuse else "false", evidence))


def main(env=None, git=default_git, fetch=fetch_json, now=None):
    env = os.environ if env is None else env
    log = []
    try:
        reuse, evidence, log = decide(env, git, fetch, now)
    except Doubt as doubt:
        reuse, evidence = False, ""
        log.append("doubt ({0})".format(doubt))
    except Exception:
        reuse, evidence = False, ""
        log.append("doubt (error)")
    for line in log:
        print("tree-receipt verify: " + line)
    print("tree-receipt verify: reuse={0}".format("true" if reuse else "false"))
    write_output(env.get("GITHUB_OUTPUT"), reuse, evidence)
    return 0


if __name__ == "__main__":
    sys.exit(main())
