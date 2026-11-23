import contextlib
import datetime
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "actions" / "tree-receipt"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load("tree_receipt_verify", BASE / "verify" / "verify.py")
record = load("tree_receipt_record", BASE / "record" / "record.py")

REPO = "CodesWhat/example"
SERVER = "https://github.com"
API = "https://api.github.com"
WF = ".github/workflows/ci.yml"
WF_REF = "{0}/{1}@refs/pull/7/merge".format(REPO, WF)
TREE = "a" * 40
OTHER_TREE = "b" * 40
BLOB = "c" * 40
OTHER_BLOB = "d" * 40
MAIN = "e" * 40
HEAD = "1" * 40
PARENT_A = "2" * 40
PARENT_B = "3" * 40
NOW = datetime.datetime(2026, 11, 22, 12, 0, 0, tzinfo=datetime.timezone.utc)
FRESH = "2026-11-21T12:00:00Z"
OLD = "2026-11-01T12:00:00Z"


def run_url(run_id=500):
    return "{0}/{1}/actions/runs/{2}".format(SERVER, REPO, run_id)


def status(tree=TREE, context="tree-receipt/" + WF, state="success",
           login="github-actions[bot]", target="default"):
    return {
        "context": context, "state": state, "description": "tree=" + tree,
        "creator": {"login": login},
        "target_url": run_url() if target == "default" else target,
    }


def run_obj(sha, **over):
    data = {
        "repository": {"full_name": REPO}, "path": WF + "@refs/pull/7/merge",
        "event": "push", "conclusion": "success", "status": "completed", "head_sha": sha,
        "created_at": FRESH,
    }
    data.update(over)
    return data


def jobs(*conclusions):
    items = [{"conclusion": c} for c in conclusions]
    return {"total_count": len(items), "jobs": items}


class Pages:
    def __init__(self, *pages):
        self.pages = pages


class Api:
    """Mock fetch: routes keyed by path, optionally paged."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, auth):
        self.calls.append(url)
        assert url.startswith(API), url
        rest = url[len(API):]
        path, _, query = rest.partition("?")
        page = 1
        match = re.search(r"(?:^|&)page=([0-9]+)", query)
        if match:
            page = int(match.group(1))
        key = rest if rest in self.routes else path
        if key not in self.routes:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        value = self.routes[key]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, Pages):
            return value.pages[page - 1] if page <= len(value.pages) else []
        return value


def git_stub(tree=TREE, head=HEAD, parents=(PARENT_A,), shallow="false"):
    def git(args):
        if len(args) == 2 and args[0] == "rev-parse" and args[1].endswith("^{tree}"):
            return tree
        if args == ["rev-parse", "--is-shallow-repository"]:
            return shallow
        if args == ["rev-parse", "HEAD"]:
            return head
        if args == ["rev-parse", "HEAD^@"]:
            return "\n".join(parents)
        raise AssertionError(args)
    return git


def stat_path(sha):
    return "/repos/{0}/commits/{1}/statuses".format(REPO, sha)


def run_path(run_id=500):
    return "/repos/{0}/actions/runs/{1}".format(REPO, run_id)


def commit_path(sha):
    return "/repos/{0}/git/commits/{1}".format(REPO, sha)


def contents_path(ref):
    return "/repos/{0}/contents/{1}?ref={2}".format(REPO, WF, ref)


REPO_PATH = "/repos/" + REPO
BRANCH_PATH = "/repos/{0}/branches/main".format(REPO)


def good_routes(sha=PARENT_A, run_id=500):
    return {
        "/repos/{0}/commits/{1}/pulls".format(REPO, HEAD): [],
        stat_path(sha): [status(target=run_url(run_id))],
        run_path(run_id): run_obj(sha),
        run_path(run_id) + "/jobs": jobs("success", "success"),
        commit_path(sha): {"tree": {"sha": TREE}},
        REPO_PATH: {"default_branch": "main"},
        BRANCH_PATH: {"commit": {"sha": MAIN}},
        contents_path(MAIN): {"type": "file", "sha": BLOB},
        contents_path(sha): {"type": "file", "sha": BLOB},
    }


def base_env(tmp, event="push", pr_head=None, **extra):
    env = {
        "TREE_RECEIPT_EVENT_NAME": event,
        "TREE_RECEIPT_AUTH": "secret-value",
        "GITHUB_REPOSITORY": REPO,
        "GITHUB_WORKFLOW_REF": WF_REF,
        "GITHUB_SERVER_URL": SERVER,
        "GITHUB_API_URL": API,
        "GITHUB_OUTPUT": str(Path(tmp) / "output"),
        "GITHUB_EVENT_PATH": str(Path(tmp) / "event.json"),
        "GITHUB_RUN_ID": "900",
        "GITHUB_SHA": HEAD,
    }
    pr = {"head": {"sha": pr_head or HEAD, "repo": {"full_name": REPO}},
          "base": {"repo": {"full_name": REPO}}}
    Path(env["GITHUB_EVENT_PATH"]).write_text(json.dumps({"pull_request": pr}))
    env.update(extra)
    return env


def run_verify(routes, event="push", git=None, pr_head=None, now=NOW, **extra):
    with tempfile.TemporaryDirectory() as tmp:
        env = base_env(tmp, event, pr_head, **extra)
        api = Api(routes)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            verify.main(env, git or git_stub(), api, now)
        out = Path(env["GITHUB_OUTPUT"]).read_text()
        return out, buf.getvalue(), api


def reuse_of(out):
    return re.search(r"^reuse=(.*)$", out, re.M).group(1)


class VerifyTests(unittest.TestCase):
    def assertReuse(self, expected, out):
        self.assertEqual(expected, reuse_of(out), out)

    def test_valid_receipt_on_parent_reuses(self):
        out, log, api = run_verify(good_routes())
        self.assertReuse("true", out)
        self.assertIn(contents_path(MAIN), [c[len(API):] for c in api.calls])
        self.assertIn("evidence-run=" + run_url() + "\n", out)
        self.assertIn("candidate=" + PARENT_A, log)
        self.assertIn("created_at=" + FRESH, log)
        self.assertNotIn("secret-value", log)

    def test_pull_request_uses_head_sha(self):
        out, _, api = run_verify(good_routes(PARENT_B), event="pull_request", pr_head=PARENT_B)
        self.assertReuse("true", out)
        self.assertFalse(any("/pulls" in c for c in api.calls))

    def test_tree_mismatch(self):
        routes = good_routes()
        routes[stat_path(PARENT_A)] = [status(tree=OTHER_TREE)]
        self.assertReuse("false", run_verify(routes)[0])

    def test_different_workflow_context(self):
        routes = good_routes()
        routes[stat_path(PARENT_A)] = [status(context="tree-receipt/.github/workflows/other.yml")]
        self.assertReuse("false", run_verify(routes)[0])

    def test_run_path_mismatch(self):
        routes = good_routes()
        routes[run_path(500)] = run_obj(PARENT_A, path=".github/workflows/other.yml")
        self.assertReuse("false", run_verify(routes)[0])

    def test_run_path_without_ref_suffix_accepted(self):
        routes = good_routes()
        routes[run_path(500)] = run_obj(PARENT_A, path=WF)
        self.assertReuse("true", run_verify(routes)[0])

    def test_creator_not_bot(self):
        for login in ("someone", "github-actions", "Github-Actions[bot]", None):
            with self.subTest(login=login):
                routes = good_routes()
                routes[stat_path(PARENT_A)] = [status(login=login)]
                self.assertReuse("false", run_verify(routes)[0])

    def test_state_not_success(self):
        routes = good_routes()
        routes[stat_path(PARENT_A)] = [status(state="pending")]
        self.assertReuse("false", run_verify(routes)[0])

    def test_target_url_other_repo_or_malformed(self):
        for target in (
                SERVER + "/CodesWhat/other/actions/runs/500",
                run_url() + "/attempts/1",
                run_url() + "x",
                "https://evil.example/{0}/actions/runs/500".format(REPO),
                SERVER + "/" + REPO + "/actions/runs/abc",
                None, ""):
            with self.subTest(target=target):
                routes = good_routes()
                routes[stat_path(PARENT_A)] = [status(target=target)]
                self.assertReuse("false", run_verify(routes)[0])

    def test_run_failures(self):
        cases = {
            "conclusion": run_obj(PARENT_A, conclusion="failure"),
            "status": run_obj(PARENT_A, status="in_progress"),
            "head_sha": run_obj(PARENT_A, head_sha=PARENT_B),
            "no-event": run_obj(PARENT_A, event=None),
            "repo": run_obj(PARENT_A, repository={"full_name": "CodesWhat/other"}),
            "old": run_obj(PARENT_A, created_at=OLD),
            "future": run_obj(PARENT_A, created_at="2026-11-23T12:00:00Z"),
        }
        for name, obj in cases.items():
            with self.subTest(name=name):
                routes = good_routes()
                routes[run_path(500)] = obj
                self.assertReuse("false", run_verify(routes)[0])

    def test_max_age_input(self):
        out, _, _ = run_verify(good_routes(), TREE_RECEIPT_MAX_AGE_HOURS="12")
        self.assertReuse("false", out)
        out, _, _ = run_verify(good_routes(), TREE_RECEIPT_MAX_AGE_HOURS="48")
        self.assertReuse("true", out)
        for bad in ("abc", "0", "-3", "nan"):
            with self.subTest(bad=bad):
                out, _, _ = run_verify(good_routes(), TREE_RECEIPT_MAX_AGE_HOURS=bad)
                self.assertReuse("false", out)

    def test_bad_jobs(self):
        for conclusion in ("failure", "cancelled", "timed_out", "action_required",
                           "startup_failure", "skipped", "neutral", "stale", None, ""):
            with self.subTest(conclusion=conclusion):
                routes = good_routes()
                routes[run_path(500) + "/jobs"] = jobs("success", conclusion)
                self.assertReuse("false", run_verify(routes)[0])

    def test_incomplete_or_empty_jobs(self):
        routes = good_routes()
        routes[run_path(500) + "/jobs"] = {"total_count": 3, "jobs": [{"conclusion": "success"}]}
        self.assertReuse("false", run_verify(routes)[0])
        routes[run_path(500) + "/jobs"] = jobs()
        self.assertReuse("false", run_verify(routes)[0])

    def test_bad_job_on_second_page(self):
        routes = good_routes()
        first = {"total_count": 101, "jobs": [{"conclusion": "success"}] * 100}
        second = {"total_count": 101, "jobs": [{"conclusion": "failure"}]}
        routes[run_path(500) + "/jobs"] = Pages(first, second)
        self.assertReuse("false", run_verify(routes)[0])

    def test_api_error_at_each_step(self):
        for key in (stat_path(PARENT_A), run_path(500), run_path(500) + "/jobs",
                    commit_path(PARENT_A), REPO_PATH, BRANCH_PATH,
                    contents_path(MAIN), contents_path(PARENT_A)):
            with self.subTest(step=key):
                routes = good_routes()
                routes[key] = OSError("boom")
                self.assertReuse("false", run_verify(routes)[0])
            with self.subTest(step=key, missing=True):
                routes = good_routes()
                del routes[key]
                self.assertReuse("false", run_verify(routes)[0])

    def test_api_error_on_pulls_lookup(self):
        routes = good_routes()
        routes["/repos/{0}/commits/{1}/pulls".format(REPO, HEAD)] = OSError("boom")
        self.assertReuse("false", run_verify(routes)[0])

    def test_malformed_data(self):
        for key, bad in ((stat_path(PARENT_A), {"x": 1}), (stat_path(PARENT_A), ["junk"]),
                         (run_path(500), []), (run_path(500) + "/jobs", [])):
            with self.subTest(key=key, bad=bad):
                routes = good_routes()
                routes[key] = bad
                self.assertReuse("false", run_verify(routes)[0])

    def test_other_events_never_reuse(self):
        for event in ("schedule", "workflow_dispatch", "merge_group", "workflow_call", ""):
            with self.subTest(event=event):
                out, _, api = run_verify(good_routes(), event=event)
                self.assertReuse("false", out)
                self.assertEqual([], api.calls)

    def test_no_git_repo(self):
        def git(args):
            raise subprocess.CalledProcessError(128, "git")
        self.assertReuse("false", run_verify(good_routes(), git=git)[0])

    def test_missing_auth_and_bad_workflow_ref(self):
        self.assertReuse("false", run_verify(good_routes(), TREE_RECEIPT_AUTH="")[0])
        self.assertReuse("false", run_verify(good_routes(), GITHUB_WORKFLOW_REF="junk")[0])

    def test_push_merge_commit_finds_dev_head_receipt(self):
        git = git_stub(parents=(PARENT_A, PARENT_B))
        routes = good_routes(PARENT_B)
        routes[stat_path(PARENT_A)] = []
        routes["/repos/{0}/commits/{1}/pulls".format(REPO, HEAD)] = []
        out, log, _ = run_verify(routes, git=git)
        self.assertReuse("true", out)
        self.assertIn("candidate=" + PARENT_B, log)

    def test_push_associated_pr_head(self):
        pr_head = "4" * 40
        routes = good_routes(pr_head)
        routes[stat_path(PARENT_A)] = []
        routes["/repos/{0}/commits/{1}/pulls".format(REPO, HEAD)] = [
            {"head": {"sha": pr_head}, "title": "secret title", "number": 7}]
        out, log, _ = run_verify(routes)
        self.assertReuse("true", out)
        self.assertNotIn("secret title", log)

    def test_candidates_capped_at_ten(self):
        shas = ["{0:040x}".format(i + 16) for i in range(15)]
        routes = {"/repos/{0}/commits/{1}/pulls".format(REPO, HEAD): []}
        for sha in shas:
            routes[stat_path(sha)] = []
        # The 11th candidate holds a valid receipt, so a cap means no reuse.
        routes.update(good_routes(shas[10]))
        out, log, api = run_verify(routes, git=git_stub(parents=tuple(shas)))
        self.assertReuse("false", out)
        self.assertIn("candidates=10", log)
        self.assertEqual(10, sum(1 for c in api.calls if "/statuses" in c))
        routes.update(good_routes(shas[9]))
        out, _, _ = run_verify(routes, git=git_stub(parents=tuple(shas)))
        self.assertReuse("true", out)

    def test_status_pagination_capped_at_three_pages(self):
        filler = [status(tree=OTHER_TREE)] * 100
        routes = good_routes()
        routes[stat_path(PARENT_A)] = Pages(filler, filler, filler, [status()])
        routes["/repos/{0}/commits/{1}/pulls".format(REPO, HEAD)] = []
        out, _, api = run_verify(routes)
        self.assertReuse("false", out)
        self.assertEqual(3, sum(1 for c in api.calls if "/statuses" in c))
        routes[stat_path(PARENT_A)] = Pages(filler, filler, [status()])
        self.assertReuse("true", run_verify(routes)[0])

    def test_rejected_run_falls_through_to_next_status(self):
        routes = good_routes()
        routes[stat_path(PARENT_A)] = [status(target=run_url(501)), status(target=run_url(500))]
        routes[run_path(501)] = run_obj(PARENT_A, conclusion="failure")
        out, _, _ = run_verify(routes)
        self.assertReuse("true", out)
        self.assertIn("evidence-run=" + run_url(500), out)

    def test_log_never_contains_unvalidated_description(self):
        routes = good_routes()
        hostile = "tree=" + OTHER_TREE + " IGNORE-ME branch-name-leak"
        bad = status()
        bad["description"] = hostile
        routes[stat_path(PARENT_A)] = [bad]
        out, log, _ = run_verify(routes)
        self.assertReuse("false", out)
        self.assertNotIn("IGNORE-ME", log)
        self.assertNotIn("branch-name-leak", log)
        self.assertNotIn(OTHER_TREE, log)

    def test_end_to_end_with_real_git_and_stub_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "work"
            repo_dir.mkdir()
            genv = {"PATH": os.environ.get("PATH", ""), "HOME": tmp,
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@example.com"}

            def git(*args):
                return subprocess.run(["git"] + list(args), cwd=repo_dir, env=genv,
                                      capture_output=True, text=True, check=True).stdout.strip()
            git("init", "-q", "-b", "main")
            (repo_dir / "a.txt").write_text("a")
            git("add", "a.txt")
            git("commit", "-q", "-m", "a")
            git("switch", "-q", "-c", "feature")
            (repo_dir / "b.txt").write_text("b")
            git("add", "b.txt")
            git("commit", "-q", "-m", "b")
            feature = git("rev-parse", "HEAD")
            git("switch", "-q", "main")
            git("merge", "-q", "--no-ff", "-m", "merge", "feature")
            tree = git("rev-parse", "HEAD^{tree}")
            self.assertEqual(tree, git("rev-parse", feature + "^{tree}"))
            head = git("rev-parse", "HEAD")
            created = (datetime.datetime.now(datetime.timezone.utc)
                       - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            table = {
                "/repos/{0}/commits/{1}/pulls".format(REPO, head): [],
                stat_path(feature): [status(tree=tree)],
                run_path(500): run_obj(feature, created_at=created),
                run_path(500) + "/jobs": jobs("success"),
                commit_path(feature): {"tree": {"sha": tree}},
                REPO_PATH: {"default_branch": "main"},
                BRANCH_PATH: {"commit": {"sha": MAIN}},
                "/repos/{0}/contents/{1}".format(REPO, WF): {"type": "file", "sha": BLOB},
            }

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    path = self.path.split("?")[0]
                    if self.headers.get("Authorization") != "Bearer secret-value":
                        self.send_response(401)
                        self.end_headers()
                        return
                    if path.endswith("/statuses") and path != stat_path(feature):
                        body = b"[]"
                    elif path in table:
                        body = json.dumps(table[path]).encode()
                    else:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *args):
                    pass

            server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                out = Path(tmp) / "output"
                env = {
                    "PATH": os.environ.get("PATH", ""), "HOME": tmp,
                    "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                    "TREE_RECEIPT_EVENT_NAME": "push", "TREE_RECEIPT_AUTH": "secret-value",
                    "TREE_RECEIPT_MAX_AGE_HOURS": "168",
                    "GITHUB_REPOSITORY": REPO, "GITHUB_WORKFLOW_REF": WF_REF,
                    "GITHUB_SERVER_URL": SERVER,
                    "GITHUB_API_URL": "http://127.0.0.1:{0}".format(server.server_address[1]),
                    "GITHUB_OUTPUT": str(out),
                }
                proc = subprocess.run(
                    [sys.executable, "-I", str(BASE / "verify" / "verify.py")],
                    cwd=repo_dir, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertEqual("reuse=true\nevidence-run={0}\n".format(run_url()), out.read_text())
                self.assertIn(feature, proc.stdout)
                self.assertNotIn("secret-value", proc.stdout + proc.stderr)
                # A new commit changes the tree, so the same receipt no longer matches.
                (repo_dir / "c.txt").write_text("c")
                git("add", "c.txt")
                git("commit", "-q", "-m", "c")
                out.unlink()
                proc = subprocess.run(
                    [sys.executable, "-I", str(BASE / "verify" / "verify.py")],
                    cwd=repo_dir, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual("reuse=false\nevidence-run=\n", out.read_text())
            finally:
                server.shutdown()
                server.server_close()

    def test_forged_description_with_other_tree_commit(self):
        # Green push run, receipt claims TREE, but the run's commit has another tree.
        routes = good_routes()
        routes[commit_path(PARENT_A)] = {"tree": {"sha": OTHER_TREE}}
        self.assertReuse("false", run_verify(routes)[0])
        routes = good_routes(PARENT_B)
        routes[commit_path(PARENT_B)] = {"tree": {"sha": OTHER_TREE}}
        out, _, _ = run_verify(routes, event="pull_request", pr_head=PARENT_B)
        self.assertReuse("false", out)

    def test_positive_controls(self):
        pr_head = "4" * 40
        routes = good_routes(pr_head)
        routes[stat_path(PARENT_A)] = []
        routes["/repos/{0}/commits/{1}/pulls".format(REPO, HEAD)] = [{"head": {"sha": pr_head}}]
        self.assertReuse("true", run_verify(routes)[0])
        # Promotion-style push: merge commit with main and dev parents.
        routes = good_routes(PARENT_B)
        routes[stat_path(PARENT_A)] = []
        out, log, _ = run_verify(routes, git=git_stub(parents=(PARENT_A, PARENT_B)))
        self.assertReuse("true", out)
        self.assertIn("candidate=" + PARENT_B, log)
        # Pull request caller citing a push run on the PR head.
        out, _, _ = run_verify(good_routes(PARENT_B), event="pull_request", pr_head=PARENT_B)
        self.assertReuse("true", out)

    def test_workflow_blob_differs_from_default_branch(self):
        routes = good_routes()
        routes[contents_path(PARENT_A)] = {"type": "file", "sha": OTHER_BLOB}
        self.assertReuse("false", run_verify(routes)[0])

    def test_default_branch_compared_by_head_sha_not_name(self):
        routes = good_routes()
        del routes[contents_path(MAIN)]
        routes["/repos/{0}/contents/{1}?ref=main".format(REPO, WF)] = {"sha": BLOB}
        self.assertReuse("false", run_verify(routes)[0])

    def test_malformed_lookups(self):
        bad_values = ({}, [], None, {"sha": "xyz"}, {"sha": 5}, {"tree": {}},
                      {"tree": {"sha": "short"}}, {"default_branch": ""},
                      {"commit": {"sha": "short"}}, "text")
        for key in (commit_path(PARENT_A), REPO_PATH, BRANCH_PATH,
                    contents_path(MAIN), contents_path(PARENT_A)):
            for bad in bad_values:
                with self.subTest(key=key, bad=bad):
                    routes = good_routes()
                    routes[key] = bad
                    self.assertReuse("false", run_verify(routes)[0])

    def test_non_push_source_runs_rejected(self):
        for event in ("pull_request", "pull_request_target", "schedule",
                      "workflow_dispatch", "merge_group", "workflow_call", 5):
            with self.subTest(event=event):
                routes = good_routes()
                routes[run_path(500)] = run_obj(PARENT_A, event=event)
                out, _, api = run_verify(routes)
                self.assertReuse("false", out)
                self.assertFalse(any("/jobs" in c or "/git/commits" in c for c in api.calls))

    def test_skipped_job_rejected_all_success_accepted(self):
        routes = good_routes()
        routes[run_path(500) + "/jobs"] = jobs("success", "skipped")
        self.assertReuse("false", run_verify(routes)[0])
        routes[run_path(500) + "/jobs"] = jobs("success", "success", "success")
        self.assertReuse("true", run_verify(routes)[0])

    def test_run_checks_capped_at_five(self):
        def routes_for(valid):
            routes = good_routes()
            routes[stat_path(PARENT_A)] = [status(target=run_url(500 + i)) for i in range(6)]
            for i in range(6):
                routes[run_path(500 + i)] = run_obj(PARENT_A, conclusion="failure")
                routes[run_path(500 + i) + "/jobs"] = jobs("success")
            routes[run_path(500 + valid)] = run_obj(PARENT_A)
            return routes
        out, log, api = run_verify(routes_for(5))
        self.assertReuse("false", out)
        self.assertIn("run check limit reached", log)
        self.assertNotIn(run_path(505), [c[len(API):] for c in api.calls])
        out, _, _ = run_verify(routes_for(4))
        self.assertReuse("true", out)

    def test_duplicate_runs_checked_once(self):
        routes = good_routes()
        routes[stat_path(PARENT_A)] = [status(), status(), status()]
        routes[run_path(500)] = run_obj(PARENT_A, conclusion="failure")
        out, _, api = run_verify(routes)
        self.assertReuse("false", out)
        self.assertEqual(1, sum(1 for c in api.calls if c.endswith("/actions/runs/500")))

    def test_verify_deadline(self):
        for last, expected in ((120, "true"), (121, "false")):
            with self.subTest(last=last):
                with unittest.mock.patch.object(verify.time, "monotonic",
                                                side_effect=[0, last]):
                    out, log, _ = run_verify(good_routes())
                self.assertReuse(expected, out)
                if expected == "false":
                    self.assertIn("deadline", log)

    def test_malformed_pulls_response(self):
        for bad in ({"x": 1}, "text", [{"head": {}}], [{}], [None], [{"head": {"sha": 5}}]):
            with self.subTest(bad=bad):
                routes = good_routes()
                routes["/repos/{0}/commits/{1}/pulls".format(REPO, HEAD)] = bad
                self.assertReuse("false", run_verify(routes)[0])

    def test_invalid_candidate_sha_rejected_before_status_lookup(self):
        for bad in ("abc", "g" * 40, "A" * 40, "1" * 39, "1" * 41, 12, None):
            with self.subTest(bad=bad, source="parent"):
                git = git_stub(parents=(bad,)) if isinstance(bad, str) else None
                if git is None:
                    continue
                out, _, api = run_verify(good_routes(), git=git)
                self.assertReuse("false", out)
                self.assertFalse(any("/statuses" in c for c in api.calls))
            with self.subTest(bad=bad, source="pull"):
                routes = good_routes()
                routes["/repos/{0}/commits/{1}/pulls".format(REPO, HEAD)] = [
                    {"head": {"sha": bad}}]
                out, _, api = run_verify(routes)
                self.assertReuse("false", out)
                self.assertFalse(any("/statuses" in c for c in api.calls))
            with self.subTest(bad=bad, source="pr-event"):
                with tempfile.TemporaryDirectory() as tmp:
                    env = base_env(tmp, "pull_request")
                    Path(env["GITHUB_EVENT_PATH"]).write_text(json.dumps(
                        {"pull_request": {"head": {"sha": bad}}}))
                    api = Api(good_routes())
                    with contextlib.redirect_stdout(io.StringIO()):
                        verify.main(env, git_stub(), api, NOW)
                    self.assertReuse("false", Path(env["GITHUB_OUTPUT"]).read_text())
                    self.assertFalse(any("/statuses" in c for c in api.calls))

    def test_bad_created_at_rejected_without_jobs_fetch(self):
        for bad in (None, "", "yesterday", 5, "2026-11-21", "2026-11-21T12:00:00+00:00"):
            with self.subTest(bad=bad):
                routes = good_routes()
                routes[run_path(500)] = run_obj(PARENT_A, created_at=bad)
                out, _, api = run_verify(routes)
                self.assertReuse("false", out)
                self.assertFalse(any("/jobs" in c for c in api.calls))
        routes = good_routes()
        run = run_obj(PARENT_A)
        del run["created_at"]
        routes[run_path(500)] = run
        out, _, api = run_verify(routes)
        self.assertReuse("false", out)
        self.assertFalse(any("/jobs" in c for c in api.calls))

    def test_malformed_pull_request_event_makes_no_api_calls(self):
        for content in ("not json", "[]", "{}", json.dumps({"pull_request": []}),
                        json.dumps({"pull_request": {"head": {}}}), None):
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as tmp:
                    env = base_env(tmp, "pull_request")
                    if content is None:
                        os.unlink(env["GITHUB_EVENT_PATH"])
                    else:
                        Path(env["GITHUB_EVENT_PATH"]).write_text(content)
                    api = Api(good_routes())
                    with contextlib.redirect_stdout(io.StringIO()):
                        verify.main(env, git_stub(), api, NOW)
                    self.assertReuse("false", Path(env["GITHUB_OUTPUT"]).read_text())
                    self.assertEqual([], api.calls)

    def test_job_pagination_limit(self):
        def page(total):
            return {"total_count": total, "jobs": [{"conclusion": "success"}] * 100}
        routes = good_routes()
        routes[run_path(500) + "/jobs"] = Pages(*[page(501)] * 6)
        out, _, api = run_verify(routes)
        self.assertReuse("false", out)
        self.assertEqual(5, sum(1 for c in api.calls if "/jobs" in c))
        routes[run_path(500) + "/jobs"] = Pages(*[page(500)] * 6)
        out, _, api = run_verify(routes)
        self.assertReuse("true", out)
        self.assertEqual(5, sum(1 for c in api.calls if "/jobs" in c))

    def test_shallow_clone_logs_and_keeps_pr_head_candidates(self):
        pr_head = "4" * 40
        routes = good_routes(pr_head)
        routes["/repos/{0}/commits/{1}/pulls".format(REPO, HEAD)] = [{"head": {"sha": pr_head}}]
        out, log, _ = run_verify(routes, git=git_stub(parents=(), shallow="true"))
        self.assertReuse("true", out)
        self.assertIn("shallow checkout, parents unavailable", log)
        out, log, _ = run_verify(routes, git=git_stub(parents=(), shallow="false"))
        self.assertNotIn("shallow checkout", log)

    def test_redirect_is_refused_without_following(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = base_env(tmp, "push", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1",
                               GITHUB_API_URL="http://127.0.0.1:{0}".format(
                                   server.server_address[1]))
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    verify.main(env, git_stub(), verify.fetch_json, NOW)
                out = Path(env["GITHUB_OUTPUT"]).read_text()
            self.assertReuse("false", out)
            self.assertIn("redirect", buf.getvalue())
            self.assertEqual(1, len(seen), seen)
        finally:
            server.shutdown()
            server.server_close()

    def test_readme_wiring_guards_verify_failure(self):
        readme = (BASE / "README.md").read_text(encoding="utf-8")
        self.assertIn("needs.verify.result != 'success'", readme)
        self.assertIn("!cancelled()", readme)
        self.assertIn("fetch-depth: 2", readme)
        self.assertNotIn("tested-tree", readme.replace("`tested-tree` input", ""))

    def test_action_shape(self):
        action = (BASE / "verify" / "action.yml").read_text(encoding="utf-8")
        for expected in ("using: composite", 'default: "168"',
                         "default: ${{ github.token }}",
                         "value: ${{ steps.verify.outputs.reuse }}",
                         "value: ${{ steps.verify.outputs.evidence-run }}",
                         "TREE_RECEIPT_MAX_AGE_HOURS: ${{ inputs.max-age-hours }}",
                         "TREE_RECEIPT_AUTH: ${{ inputs.github-token }}",
                         "TREE_RECEIPT_EVENT_NAME: ${{ github.event_name }}",
                         'run: python3 -I "$GITHUB_ACTION_PATH/verify.py"'):
            self.assertIn(expected, action)
        for line in action.splitlines():
            if line.strip().startswith("run:"):
                self.assertNotIn("${{", line)
        self.assertNotIn("uses:", action)


class Recorder:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def __call__(self, url, auth, payload):
        self.calls.append((url, auth, payload))
        if self.fail:
            raise OSError("boom")


def run_record(event="push", git=None, recorder=None, event_data=None, **extra):
    recorder = recorder or Recorder()
    with tempfile.TemporaryDirectory() as tmp:
        env = base_env(tmp, event, **extra)
        if event_data is not None:
            Path(env["GITHUB_EVENT_PATH"]).write_text(json.dumps(event_data))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = record.main(env, git or git_stub(), recorder)
    return code, buf.getvalue(), recorder


class RecordTests(unittest.TestCase):
    def test_push_writes_receipt_on_github_sha(self):
        code, log, rec = run_record()
        self.assertEqual(0, code)
        (url, auth, payload), = rec.calls
        self.assertEqual("{0}/repos/{1}/statuses/{2}".format(API, REPO, HEAD), url)
        self.assertEqual("secret-value", auth)
        self.assertEqual({
            "state": "success", "target_url": run_url(900),
            "description": "tree=" + TREE, "context": "tree-receipt/" + WF,
        }, payload)
        self.assertNotIn("secret-value", log)

    def test_tree_comes_from_github_sha_commit(self):
        asked = []
        def git(args):
            asked.append(args)
            return OTHER_TREE
        _, _, rec = run_record(git=git)
        self.assertEqual([["rev-parse", HEAD + "^{tree}"]], asked)
        self.assertEqual("tree=" + OTHER_TREE, rec.calls[0][2]["description"])

    def test_tested_tree_override_is_ignored(self):
        _, _, rec = run_record(TREE_RECEIPT_TESTED_TREE=OTHER_TREE, git=git_stub(tree=TREE))
        self.assertEqual("tree=" + TREE, rec.calls[0][2]["description"])

    def test_non_push_events_write_nothing(self):
        pr = {"pull_request": {"head": {"sha": HEAD, "repo": {"full_name": REPO}},
                               "base": {"repo": {"full_name": REPO}}}}
        for event in ("pull_request", "pull_request_target", "schedule",
                      "workflow_dispatch", "merge_group", "workflow_call", ""):
            with self.subTest(event=event):
                code, log, rec = run_record(event=event, event_data=pr)
                self.assertEqual(0, code)
                self.assertEqual([], rec.calls)
                self.assertIn("skipped (event", log)

    def test_write_failure_exits_zero(self):
        code, log, _ = run_record(recorder=Recorder(fail=True))
        self.assertEqual(0, code)
        self.assertIn("write failed", log)

    def test_invalid_tree_from_git_rejected(self):
        for bad in ("abc", "A" * 40, "g" * 40, "a" * 41, "a" * 40 + "\n--", TREE[:-1], ""):
            with self.subTest(tree=bad):
                code, log, rec = run_record(git=git_stub(tree=bad))
                self.assertEqual(0, code)
                self.assertEqual([], rec.calls)
                self.assertIn("invalid-tree", log)

    def test_invalid_github_sha_skips(self):
        for bad in ("abc", "G" * 40, HEAD + "0", ""):
            with self.subTest(sha=bad):
                _, log, rec = run_record(GITHUB_SHA=bad)
                self.assertEqual([], rec.calls)
                self.assertIn("skipped (sha)", log)

    def test_git_failure_skips(self):
        def git(args):
            raise subprocess.CalledProcessError(128, "git")
        code, log, rec = run_record(git=git)
        self.assertEqual(0, code)
        self.assertEqual([], rec.calls)
        self.assertIn("no-tree", log)

    def test_bad_workflow_ref_or_run_id_skips(self):
        for extra in ({"GITHUB_WORKFLOW_REF": "junk"}, {"GITHUB_RUN_ID": "abc"},
                      {"TREE_RECEIPT_AUTH": ""}, {"GITHUB_REPOSITORY": "a/b/c"}):
            with self.subTest(extra=extra):
                code, _, rec = run_record(**extra)
                self.assertEqual(0, code)
                self.assertEqual([], rec.calls)

    def test_end_to_end_with_stub_server(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                seen.append((self.path, self.headers.get("Authorization"),
                             json.loads(self.rfile.read(length))))
                self.send_response(201)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                genv = {"PATH": os.environ.get("PATH", ""), "HOME": tmp,
                        "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.com"}
                work = Path(tmp) / "work"
                work.mkdir()

                def git(*args):
                    return subprocess.run(["git"] + list(args), cwd=work, env=genv,
                                          capture_output=True, text=True,
                                          check=True).stdout.strip()
                git("init", "-q", "-b", "main")
                (work / "a.txt").write_text("a")
                git("add", "a.txt")
                git("commit", "-q", "-m", "a")
                sha = git("rev-parse", "HEAD")
                tree = git("rev-parse", "HEAD^{tree}")
                env = {
                    "PATH": os.environ.get("PATH", ""), "HOME": tmp,
                    "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                    "TREE_RECEIPT_EVENT_NAME": "push", "TREE_RECEIPT_AUTH": "secret-value",
                    "GITHUB_REPOSITORY": REPO, "GITHUB_WORKFLOW_REF": WF_REF,
                    "GITHUB_SERVER_URL": SERVER, "GITHUB_RUN_ID": "900", "GITHUB_SHA": sha,
                    "GITHUB_API_URL": "http://127.0.0.1:{0}".format(server.server_address[1]),
                }
                proc = subprocess.run(
                    [sys.executable, "-I", str(BASE / "record" / "record.py")],
                    cwd=work, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(0, proc.returncode, proc.stderr)
                (path, auth, payload), = seen
                self.assertEqual("/repos/{0}/statuses/{1}".format(REPO, sha), path)
                self.assertEqual("Bearer secret-value", auth)
                self.assertEqual("tree=" + tree, payload["description"])
                self.assertEqual("tree-receipt/" + WF, payload["context"])
                self.assertNotIn("secret-value", proc.stdout + proc.stderr)
                # An unreachable server must still exit 0.
                env["GITHUB_API_URL"] = "http://127.0.0.1:1"
                proc = subprocess.run(
                    [sys.executable, "-I", str(BASE / "record" / "record.py")],
                    cwd=work, env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(0, proc.returncode, proc.stderr)
                self.assertIn("write failed", proc.stdout)
        finally:
            server.shutdown()
            server.server_close()

    def test_redirect_is_refused_without_following(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                seen.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                env = base_env(tmp, "push", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1",
                               GITHUB_API_URL="http://127.0.0.1:{0}".format(
                                   server.server_address[1]))
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    code = record.main(env, git_stub())
            self.assertEqual(0, code)
            self.assertIn("write failed", buf.getvalue())
            self.assertEqual(1, len(seen), seen)
        finally:
            server.shutdown()
            server.server_close()

    def test_action_shape(self):
        action = (BASE / "record" / "action.yml").read_text(encoding="utf-8")
        for expected in ("using: composite", "default: ${{ github.token }}",
                         "TREE_RECEIPT_AUTH: ${{ inputs.github-token }}",
                         "TREE_RECEIPT_EVENT_NAME: ${{ github.event_name }}",
                         'run: python3 -I "$GITHUB_ACTION_PATH/record.py"'):
            self.assertIn(expected, action)
        self.assertNotIn("tested-tree", action)
        self.assertNotIn("TESTED_TREE", action)
        for line in action.splitlines():
            if line.strip().startswith("run:"):
                self.assertNotIn("${{", line)
        self.assertNotIn("uses:", action)


if __name__ == "__main__":
    unittest.main()
