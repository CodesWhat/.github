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
        "conclusion": "success", "status": "completed", "head_sha": sha,
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
        if path not in self.routes:
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, Pages):
            return value.pages[page - 1] if page <= len(value.pages) else []
        return value


def git_stub(tree=TREE, head=HEAD, parents=(PARENT_A,)):
    def git(args):
        if args == ["rev-parse", "HEAD^{tree}"]:
            return tree
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


def good_routes(sha=PARENT_A, run_id=500):
    return {
        "/repos/{0}/commits/{1}/pulls".format(REPO, HEAD): [],
        stat_path(sha): [status(target=run_url(run_id))],
        run_path(run_id): run_obj(sha),
        run_path(run_id) + "/jobs": jobs("success", "skipped"),
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
        out, log, _ = run_verify(good_routes())
        self.assertReuse("true", out)
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
                           "startup_failure", None):
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
        for key in (stat_path(PARENT_A), run_path(500), run_path(500) + "/jobs"):
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

    def test_action_shape(self):
        action = (BASE / "verify" / "action.yml").read_text(encoding="utf-8")
        for expected in ("using: composite", 'default: "168"',
                         "default: ${{ github.token }}",
                         "value: ${{ steps.verify.outputs.reuse }}",
                         "value: ${{ steps.verify.outputs.evidence-run }}",
                         'run: python3 "$GITHUB_ACTION_PATH/verify.py"'):
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


def run_record(event="push", tree=TREE, git=None, recorder=None, event_data=None, **extra):
    recorder = recorder or Recorder()
    with tempfile.TemporaryDirectory() as tmp:
        env = base_env(tmp, event, TREE_RECEIPT_TESTED_TREE=tree, **extra)
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

    def test_pull_request_writes_on_head_sha(self):
        head = "5" * 40
        _, _, rec = run_record(event="pull_request", event_data={"pull_request": {
            "head": {"sha": head, "repo": {"full_name": REPO}},
            "base": {"repo": {"full_name": REPO}}}})
        self.assertTrue(rec.calls[0][0].endswith("/statuses/" + head))

    def test_fork_pull_request_skipped(self):
        for event in ("pull_request", "pull_request_target"):
            with self.subTest(event=event):
                code, log, rec = run_record(event=event, event_data={"pull_request": {
                    "head": {"sha": HEAD, "repo": {"full_name": "someone/example"}},
                    "base": {"repo": {"full_name": REPO}}}})
                self.assertEqual(0, code)
                self.assertEqual([], rec.calls)
                self.assertIn("skipped (fork)", log)

    def test_malformed_pull_request_event_skipped(self):
        code, _, rec = run_record(event="pull_request", event_data={"pull_request": {}})
        self.assertEqual(0, code)
        self.assertEqual([], rec.calls)

    def test_write_failure_exits_zero(self):
        code, log, _ = run_record(recorder=Recorder(fail=True))
        self.assertEqual(0, code)
        self.assertIn("write failed", log)

    def test_invalid_tested_tree_rejected(self):
        for bad in ("abc", "A" * 40, "g" * 40, "a" * 41, "a" * 40 + "\n--", TREE[:-1]):
            with self.subTest(tree=bad):
                code, log, rec = run_record(tree=bad)
                self.assertEqual(0, code)
                self.assertEqual([], rec.calls)
                self.assertIn("invalid-tree", log)

    def test_default_tree_from_git(self):
        _, _, rec = run_record(tree="", git=git_stub(tree=OTHER_TREE))
        self.assertEqual("tree=" + OTHER_TREE, rec.calls[0][2]["description"])

    def test_default_tree_git_failure_skips(self):
        def git(args):
            raise subprocess.CalledProcessError(128, "git")
        code, _, rec = run_record(tree="", git=git)
        self.assertEqual(0, code)
        self.assertEqual([], rec.calls)

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
                env = {
                    "PATH": os.environ.get("PATH", ""),
                    "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                    "TREE_RECEIPT_EVENT_NAME": "push", "TREE_RECEIPT_AUTH": "secret-value",
                    "TREE_RECEIPT_TESTED_TREE": TREE,
                    "GITHUB_REPOSITORY": REPO, "GITHUB_WORKFLOW_REF": WF_REF,
                    "GITHUB_SERVER_URL": SERVER, "GITHUB_RUN_ID": "900", "GITHUB_SHA": HEAD,
                    "GITHUB_API_URL": "http://127.0.0.1:{0}".format(server.server_address[1]),
                }
                proc = subprocess.run(
                    [sys.executable, "-I", str(BASE / "record" / "record.py")],
                    cwd=tmp, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, proc.returncode, proc.stderr)
            (path, auth, payload), = seen
            self.assertEqual("/repos/{0}/statuses/{1}".format(REPO, HEAD), path)
            self.assertEqual("Bearer secret-value", auth)
            self.assertEqual("tree=" + TREE, payload["description"])
            self.assertEqual("tree-receipt/" + WF, payload["context"])
            self.assertNotIn("secret-value", proc.stdout + proc.stderr)
            # An unreachable server must still exit 0.
            env["GITHUB_API_URL"] = "http://127.0.0.1:1"
            proc = subprocess.run(
                [sys.executable, "-I", str(BASE / "record" / "record.py")],
                cwd=tempfile.gettempdir(), env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertIn("write failed", proc.stdout)
        finally:
            server.shutdown()
            server.server_close()

    def test_action_shape(self):
        action = (BASE / "record" / "action.yml").read_text(encoding="utf-8")
        for expected in ("using: composite", "default: ${{ github.token }}",
                         "TREE_RECEIPT_TESTED_TREE: ${{ inputs.tested-tree }}",
                         'run: python3 "$GITHUB_ACTION_PATH/record.py"'):
            self.assertIn(expected, action)
        for line in action.splitlines():
            if line.strip().startswith("run:"):
                self.assertNotIn("${{", line)
        self.assertNotIn("uses:", action)


if __name__ == "__main__":
    unittest.main()
