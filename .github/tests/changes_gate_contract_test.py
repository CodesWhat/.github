import contextlib
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
ACTION_DIR = ROOT / "actions" / "changes-gate"

spec = importlib.util.spec_from_file_location("changes_gate", ACTION_DIR / "changes_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

PATTERNS = "Sources/**\n"


def entry(name, previous=None):
    item = {"filename": name}
    if previous is not None:
        item["previous_filename"] = previous
    return item


API = "https://api.github.com"
HEAD = "a" * 40


def pager(files, changed=None, head=HEAD):
    """Mock HTTP layer: the PR read plus the files listing in pages of 100."""
    total = len(files) if changed is None else changed

    def fetch(url, auth):
        if url.endswith("/pulls/7"):
            return {"changed_files": total, "head": {"sha": head}}, None
        page = 1
        if "&page=" in url:
            page = int(url.rsplit("&page=", 1)[1])
        chunk = files[(page - 1) * 100:page * 100]
        link = None
        if page * 100 < len(files):
            link = '<{0}/repos/CodesWhat/example/pulls/7/files?per_page=100&page={1}>; rel="next"'.format(
                API, page + 1)
        return chunk, link
    return fetch


def run_gate(event, files=None, patterns=PATTERNS, fetch=None, number=7, changed=None,
             head=HEAD, payload=None, write_event=True):
    with tempfile.TemporaryDirectory() as tmp:
        event_path = Path(tmp) / "event.json"
        if payload is None:
            payload = {"pull_request": {"number": number, "head": {"sha": head}}}
        if write_event:
            event_path.write_text(json.dumps(payload))
        out_path = Path(tmp) / "output"
        env = {
            "CHANGES_GATE_EVENT_NAME": event,
            "CHANGES_GATE_PATTERNS": patterns,
            "CHANGES_GATE_AUTH": "secret-value",
            "GITHUB_REPOSITORY": "CodesWhat/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_OUTPUT": str(out_path),
        }
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            gate.main(env, fetch or pager(files or [], changed))
        output = out_path.read_text() if out_path.exists() else ""
        return output, buf.getvalue()


def failing(url, auth):
    raise OSError("boom")


class ChangesGateContractTest(unittest.TestCase):
    def assertRun(self, expected, output):
        self.assertEqual("run={0}\n".format(expected), output)

    def test_non_pr_events_run(self):
        for event in ("push", "schedule", "workflow_dispatch", "merge_group",
                      "workflow_call", "something_new", ""):
            with self.subTest(event=event):
                out, _ = run_gate(event, [entry("docs/a.md")], fetch=failing)
                self.assertRun("true", out)

    def test_docs_only_pr_skips(self):
        for event in ("pull_request", "pull_request_target"):
            with self.subTest(event=event):
                out, log = run_gate(event, [entry("docs/a.md"), entry("README.md")])
                self.assertRun("false", out)
                self.assertIn("examined=2 matched=0 run=false", log)

    def test_matching_pr_runs(self):
        out, _ = run_gate("pull_request", [entry("docs/a.md"), entry("Sources/App.swift")])
        self.assertRun("true", out)

    def test_case_insensitive(self):
        out, _ = run_gate("pull_request", [entry("sources/App.swift")])
        self.assertRun("true", out)

    def test_rename_previous_filename_matches(self):
        out, _ = run_gate("pull_request", [entry("docs/App.swift", previous="Sources/App.swift")])
        self.assertRun("true", out)

    def test_globstar_and_single_star(self):
        out, _ = run_gate("pull_request", [entry("a/b/c/x.go")], patterns="**/*.go")
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("x.go")], patterns="**/*.go")
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("a/b/x.go")], patterns="*.go")
        self.assertRun("false", out)

    def test_comments_and_blank_lines_ignored(self):
        out, _ = run_gate("pull_request", [entry("docs/a.md")],
                          patterns="# note\n\n  \nSources/**\n")
        self.assertRun("false", out)

    def test_exclude_pattern(self):
        patterns = "Sources/**\n!Sources/**/*.md\n"
        out, _ = run_gate("pull_request", [entry("Sources/README.md")], patterns=patterns)
        self.assertRun("false", out)
        out, _ = run_gate("pull_request", [entry("Sources/README.md"), entry("Sources/a.swift")],
                          patterns=patterns)
        self.assertRun("true", out)

    def test_api_error_runs(self):
        out, _ = run_gate("pull_request", fetch=failing)
        self.assertRun("true", out)

    def test_unparsable_response_runs(self):
        for bad in ({"message": "nope"}, [{"nofile": 1}], [{"filename": 5}],
                    [{"filename": "a", "previous_filename": 3}], "text"):
            with self.subTest(bad=bad):
                out, _ = run_gate("pull_request", fetch=lambda u, a, b=bad: (b, None))
                self.assertRun("true", out)

    def test_three_thousand_file_cap_runs(self):
        files = [entry("docs/{0}.md".format(i)) for i in range(3000)]
        out, _ = run_gate("pull_request", files)
        self.assertRun("true", out)
        files = [entry("docs/{0}.md".format(i)) for i in range(2999)]
        out, _ = run_gate("pull_request", files)
        self.assertRun("false", out)

    def test_bracket_classes(self):
        cases = (
            ("Sources/[AB].swift", "Sources/A.swift", "true"),
            ("Sources/[AB].swift", "Sources/C.swift", "false"),
            ("Sources/[a-c].swift", "Sources/b.swift", "true"),
            ("Sources/[a-c].swift", "Sources/d.swift", "false"),
            ("Sources/[!A].swift", "Sources/B.swift", "true"),
            ("Sources/[!A].swift", "Sources/A.swift", "false"),
            ("Sources/[^A].swift", "Sources/B.swift", "true"),
            ("Sources/[^A].swift", "Sources/A.swift", "false"),
            ("Sources/[ab].swift", "Sources/A.swift", "true"),
            ("Sources/[A-C].swift", "Sources/b.swift", "true"),
            ("Sources[x]a", "Sources/a", "false"),
            ("Sources[!x]a", "Sources/a", "false"),
            ("Sources/[]].swift", "Sources/].swift", "true"),
        )
        for pattern, name, expected in cases:
            with self.subTest(pattern=pattern, name=name):
                out, _ = run_gate("pull_request", [entry(name)], patterns=pattern)
                self.assertRun(expected, out)

    def test_unparseable_glob_runs(self):
        for pattern in ("Sources/[AB.swift", "Sources/[", "Sources/[z-a].swift", "!Sources/[x"):
            with self.subTest(pattern=pattern):
                out, _ = run_gate("pull_request", [entry("docs/a.md")],
                                  patterns="Sources/**\n" + pattern)
                self.assertRun("true", out)

    def test_leading_dot_slash_and_trailing_slash(self):
        for pattern in ("./Sources/**", "././Sources/**", "Sources/", "./Sources/"):
            with self.subTest(pattern=pattern):
                out, _ = run_gate("pull_request", [entry("Sources/a/b.swift")], patterns=pattern)
                self.assertRun("true", out)
                out, _ = run_gate("pull_request", [entry("docs/a.md")], patterns=pattern)
                self.assertRun("false", out)
        out, _ = run_gate("pull_request", [entry("Sources/a.md")],
                          patterns="Sources/**\n!./Sources/*.md")
        self.assertRun("false", out)

    def test_exclude_overrides_include(self):
        patterns = "**/*.swift\n!Sources/Generated/**\n"
        out, _ = run_gate("pull_request", [entry("Sources/Generated/A.swift")], patterns=patterns)
        self.assertRun("false", out)
        out, _ = run_gate("pull_request", [entry("sources/generated/A.swift")], patterns=patterns)
        self.assertRun("true", out)

    def test_excludes_are_case_sensitive(self):
        out, _ = run_gate("pull_request", [entry("Sources/App/generated/Evil.swift")],
                          patterns="Sources/**\n!Sources/App/Generated/**")
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("Sources/App/Generated/Ok.swift")],
                          patterns="Sources/**\n!Sources/App/Generated/**")
        self.assertRun("false", out)

    def test_excludes_have_no_unicode_folding(self):
        out, _ = run_gate("pull_request", [entry("doc\u017f/x")], patterns="**\n!docs/**")
        self.assertRun("true", out)

    def test_includes_stay_case_insensitive(self):
        out, _ = run_gate("pull_request", [entry("SRC/a.js")], patterns="src/**")
        self.assertRun("true", out)

    def test_unsupported_glob_syntax_runs(self):
        for pattern in ("Sources/{a,b}/**", "Sources/a}", "{a", "/Sources/**",
                        "Sources/[[:alpha:]].swift", "!{a,b}", "!/docs/**", "Sources/**.swift/**x"):
            with self.subTest(pattern=pattern):
                out, _ = run_gate("pull_request", [entry("docs/a.md")],
                                  patterns="Sources/**\n" + pattern)
                self.assertRun("true", out)

    def test_pathological_pattern_is_bounded(self):
        pattern = "a/**/**/**/**/b*c*d*e*f*g"
        name = "a/" + "x" * 5000 + "/" + "bcdef" * 1000
        started = time.monotonic()
        out, _ = run_gate("pull_request", [entry(name)], patterns=pattern)
        elapsed = time.monotonic() - started
        self.assertRun("false", out)
        self.assertLess(elapsed, 1.0)
        print("redos elapsed {0:.4f}s".format(elapsed), file=sys.stderr)
        deep = "a/" + "/".join("s" * 20 for _ in range(300)) + "/z"
        started = time.monotonic()
        out, _ = run_gate("pull_request", [entry(deep)], patterns="a/**/*s/**/*s/**/q")
        self.assertRun("false", out)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_redirect_is_never_followed(self):
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
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old = {k: os.environ.get(k) for k in ("NO_PROXY", "no_proxy")}
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                event = Path(tmp) / "event.json"
                event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": HEAD}}}))
                out = Path(tmp) / "output"
                env = {
                    "CHANGES_GATE_EVENT_NAME": "pull_request",
                    "CHANGES_GATE_PATTERNS": PATTERNS,
                    "CHANGES_GATE_AUTH": "secret-value",
                    "GITHUB_REPOSITORY": "CodesWhat/example",
                    "GITHUB_API_URL": "http://127.0.0.1:{0}".format(server.server_address[1]),
                    "GITHUB_EVENT_PATH": str(event),
                    "GITHUB_OUTPUT": str(out),
                }
                with contextlib.redirect_stdout(io.StringIO()):
                    gate.main(env)
                self.assertRun("true", out.read_text())
        finally:
            server.shutdown()
            server.server_close()
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.assertEqual([("/repos/CodesWhat/example/pulls/7", "Bearer secret-value")], seen)

    def test_opener_refuses_redirects(self):
        handler = gate.NoRedirect()
        request = urllib.request.Request("https://api.github.com/x")
        self.assertIsNone(handler.redirect_request(
            request, None, 302, "Found", {}, "https://evil.example/y"))
        self.assertTrue(any(isinstance(h, gate.NoRedirect) for h in gate.OPENER.handlers))
        self.assertFalse(any(type(h) is urllib.request.HTTPRedirectHandler
                             for h in gate.OPENER.handlers))

    def test_head_sha_mismatch_runs(self):
        out, _ = run_gate("pull_request", fetch=pager([entry("docs/a.md")], head="b" * 40))
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("docs/a.md")])
        self.assertRun("false", out)

    def test_bad_event_head_sha_runs(self):
        for sha in (None, "", "abc", "g" * 40, 5, "a" * 41):
            with self.subTest(sha=sha):
                out, _ = run_gate("pull_request", [entry("docs/a.md")], head=sha)
                self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("docs/a.md")],
                          payload={"pull_request": {"number": 7}})
        self.assertRun("true", out)
        def no_head(url, auth):
            data, link = pager([entry("docs/a.md")])(url, auth)
            if url.endswith("/pulls/7"):
                data = {"changed_files": 1}
            return data, link
        out, _ = run_gate("pull_request", fetch=no_head)
        self.assertRun("true", out)

    def test_regex_metacharacters_are_literal(self):
        cases = (
            ("a+b.txt", "a+b.txt", "true"), ("a+b.txt", "aab.txt", "false"),
            ("a(b).txt", "a(b).txt", "true"), ("a(b).txt", "ab.txt", "false"),
            ("a.txt", "axtxt", "false"),
            ("a$b", "a$b", "true"), ("^a", "^a", "true"), ("^a", "a", "false"),
            ("a|b", "a|b", "true"), ("a|b", "a", "false"),
        )
        for pattern, name, expected in cases:
            with self.subTest(pattern=pattern, name=name):
                out, _ = run_gate("pull_request", [entry(name)], patterns=pattern)
                self.assertRun(expected, out)

    def test_odd_repository_rejected_without_fetching(self):
        for repo in ("Cod esWhat/x", "a/b/c", "../x", "x/..", "a;b/c", "a/b\nc", "noslash", ""):
            with self.subTest(repo=repo):
                calls = []

                def fetch(url, auth):
                    calls.append(url)
                    return [], None
                with tempfile.TemporaryDirectory() as tmp:
                    event = Path(tmp) / "event.json"
                    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": HEAD}}}))
                    out_path = Path(tmp) / "output"
                    env = {
                        "CHANGES_GATE_EVENT_NAME": "pull_request",
                        "CHANGES_GATE_PATTERNS": PATTERNS,
                        "CHANGES_GATE_AUTH": "secret-value",
                        "GITHUB_REPOSITORY": repo,
                        "GITHUB_EVENT_PATH": str(event),
                        "GITHUB_OUTPUT": str(out_path),
                    }
                    with contextlib.redirect_stdout(io.StringIO()):
                        gate.main(env, fetch)
                    self.assertRun("true", out_path.read_text())
                self.assertEqual([], calls)

    def test_truncated_listing_runs(self):
        out, _ = run_gate("pull_request", [entry("docs/a.md")], changed=101)
        self.assertRun("true", out)

    def test_empty_listing_with_nonzero_count_runs(self):
        out, _ = run_gate("pull_request", [], changed=3)
        self.assertRun("true", out)

    def test_count_matches_empty_listing_skips(self):
        out, _ = run_gate("pull_request", [], changed=0)
        self.assertRun("false", out)

    def test_changed_files_at_cap_runs(self):
        out, _ = run_gate("pull_request", [entry("docs/a.md")], changed=3000)
        self.assertRun("true", out)

    def test_short_page_with_rel_next_runs(self):
        base = pager([entry("docs/a.md")])

        def fetch(url, auth):
            data, _ = base(url, auth)
            if url.endswith("/pulls/7"):
                return data, None
            return data, '<{0}/repos/CodesWhat/example/pulls/7/files?page=2>; rel="next"'.format(API)
        out, _ = run_gate("pull_request", fetch=fetch)
        self.assertRun("true", out)

    def test_malformed_link_header_runs(self):
        base = pager([entry("docs/a.md")])
        for link in ("garbage", '<https://x>; rel="next", ???', "<unterminated; rel=next"):
            with self.subTest(link=link):
                def fetch(url, auth, link=link):
                    data, _ = base(url, auth)
                    return data, (None if url.endswith("/pulls/7") else link)
                out, _ = run_gate("pull_request", fetch=fetch)
                self.assertRun("true", out)

    def test_next_link_off_api_host_runs(self):
        files = [entry("docs/{0}.md".format(i)) for i in range(150)]
        base = pager(files)

        def fetch(url, auth):
            data, link = base(url, auth)
            if link:
                link = '<https://evil.example/x?page=2>; rel="next"'
            return data, link
        out, _ = run_gate("pull_request", fetch=fetch)
        self.assertRun("true", out)

    def test_failed_or_unparsable_pr_read_runs(self):
        base = pager([entry("docs/a.md")])
        bad_reads = (OSError("boom"), ["x"], {"changed_files": "1"}, {"changed_files": True},
                     {"changed_files": -1}, {})
        for bad in bad_reads:
            with self.subTest(bad=bad):
                def fetch(url, auth, bad=bad):
                    if url.endswith("/pulls/7"):
                        if isinstance(bad, Exception):
                            raise bad
                        return bad, None
                    return base(url, auth)
                out, _ = run_gate("pull_request", fetch=fetch)
                self.assertRun("true", out)

    def test_pagination_reads_every_page(self):
        files = [entry("docs/{0}.md".format(i)) for i in range(250)] + [entry("Sources/x.swift")]
        out, _ = run_gate("pull_request", files)
        self.assertRun("true", out)

    def test_empty_pattern_list_runs(self):
        for patterns in ("", "\n# only a comment\n", "!Sources/**"):
            with self.subTest(patterns=patterns):
                out, _ = run_gate("pull_request", [entry("docs/a.md")], patterns=patterns)
                self.assertRun("true", out)

    def test_missing_event_payload_runs(self):
        out, _ = run_gate("pull_request", [entry("docs/a.md")], number="nope")
        self.assertRun("true", out)

    def test_malicious_filenames_do_not_leak_or_alter_output(self):
        evil = [
            "::add-mask::true",
            "::set-output name=run::false",
            "docs/a\nrun=true\n::add-mask::x",
            "docs/\"quoted\" 'name'.md",
            "docs/a\nrun=falsy",
        ]
        files = [entry(name) for name in evil]
        out, log = run_gate("pull_request", files)
        self.assertRun("false", out)
        for name in evil:
            for fragment in name.splitlines():
                self.assertNotIn(fragment, log)
        self.assertNotIn("::", log)
        self.assertNotIn("secret-value", log)
        # A malicious name that does match still yields only the fixed line.
        out, log = run_gate("pull_request", files + [entry("Sources/::add-mask::true")])
        self.assertRun("true", out)
        self.assertNotIn("::", log)

    def test_malicious_patterns_do_not_leak(self):
        out, log = run_gate("pull_request", [entry("docs/a.md")],
                            patterns="::add-mask::true\nSources/**")
        self.assertRun("false", out)
        self.assertNotIn("::", log)

    def test_end_to_end_subprocess(self):
        files = [entry("docs/{0}.md".format(i)) for i in range(120)]
        served = pager(files)

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                host = "http://127.0.0.1:{0}".format(self.server.server_address[1])
                path = self.path.replace("/repos/", "/repos/", 1)
                data, link = served(API + path, None)
                if link:
                    link = link.replace(API, host)
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if link:
                    self.send_header("Link", link)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for pattern, expected in (("Sources/**", "false"), ("docs/119.md", "true")):
                with self.subTest(pattern=pattern), tempfile.TemporaryDirectory() as tmp:
                    event = Path(tmp) / "event.json"
                    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": HEAD}}}))
                    out = Path(tmp) / "output"
                    env = {
                        "PATH": os.environ.get("PATH", ""),
                        "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                        "CHANGES_GATE_EVENT_NAME": "pull_request",
                        "CHANGES_GATE_PATTERNS": pattern,
                        "CHANGES_GATE_AUTH": "secret-value",
                        "GITHUB_REPOSITORY": "CodesWhat/example",
                        "GITHUB_API_URL": "http://127.0.0.1:{0}".format(server.server_address[1]),
                        "GITHUB_EVENT_PATH": str(event),
                        "GITHUB_OUTPUT": str(out),
                    }
                    proc = subprocess.run(
                        [sys.executable, "-I", str(ACTION_DIR / "changes_gate.py")],
                        env=env, capture_output=True, text=True, timeout=60)
                    self.assertEqual(0, proc.returncode, proc.stderr)
                    self.assertEqual("run={0}\n".format(expected), out.read_text())
                    self.assertIn("examined=120", proc.stdout)
                    self.assertNotIn("secret-value", proc.stdout + proc.stderr)
        finally:
            server.shutdown()
            server.server_close()

    def test_readme_caller_condition_fails_open_on_gate_failure(self):
        readme = (ACTION_DIR / "README.md").read_text(encoding="utf-8")
        flat = " ".join(readme.split())
        self.assertIn("needs.changes.result != 'success'", flat)
        self.assertIn("!cancelled()", flat)
        self.assertIn("needs.changes.outputs.run != 'false'", flat)
        self.assertNotIn("if: needs.changes.outputs.run == 'true'", readme)

    def test_off_host_next_link_is_never_fetched(self):
        files = [entry("docs/{0}.md".format(i)) for i in range(150)]
        calls = []

        def fetch(url, auth):
            calls.append((url, auth))
            if url.endswith("/pulls/7"):
                return {"changed_files": 150, "head": {"sha": HEAD}}, None
            if url.startswith("https://evil.example/"):
                return files[100:], None
            return files[:100], '<https://evil.example/next?page=2>; rel="next"'
        out, _ = run_gate("pull_request", fetch=fetch)
        self.assertRun("true", out)
        self.assertEqual(
            [API + "/repos/CodesWhat/example/pulls/7",
             API + "/repos/CodesWhat/example/pulls/7/files?per_page=100"],
            [url for url, _ in calls])
        self.assertTrue(all(auth == "secret-value" for _, auth in calls))

    def test_action_output_and_env_wiring(self):
        lines = (ACTION_DIR / "action.yml").read_text(encoding="utf-8").splitlines()
        self.assertIn("    value: ${{ steps.gate.outputs.run }}", lines)
        self.assertIn("    - id: gate", lines)
        for expected in (
                "        CHANGES_GATE_PATTERNS: ${{ inputs.patterns }}",
                "        CHANGES_GATE_AUTH: ${{ inputs.github-token }}",
                "        CHANGES_GATE_EVENT_NAME: ${{ github.event_name }}"):
            self.assertIn(expected, lines)
        step = lines.index("    - id: gate")
        env = lines.index("      env:", step)
        run = next(i for i, ln in enumerate(lines) if ln.strip().startswith("run: python3"))
        self.assertTrue(step < env < run)
        for expected in ("CHANGES_GATE_PATTERNS", "CHANGES_GATE_AUTH", "CHANGES_GATE_EVENT_NAME"):
            self.assertTrue(any(expected in ln for ln in lines[env:run]))

    def test_invalid_pull_numbers_rejected_before_fetch(self):
        for number in (True, False, 0, -1):
            with self.subTest(number=number):
                calls = []

                def fetch(url, auth):
                    calls.append(url)
                    return {}, None
                out, _ = run_gate("pull_request", fetch=fetch, number=number)
                self.assertRun("true", out)
                self.assertEqual([], calls)

    def test_missing_event_file_runs(self):
        calls = []

        def fetch(url, auth):
            calls.append(url)
            return {}, None
        out, _ = run_gate("pull_request", fetch=fetch, write_event=False)
        self.assertRun("true", out)
        self.assertEqual([], calls)

    def test_missing_pull_request_key_runs(self):
        for payload in ({}, {"pull_request": {}}, {"pull_request": {"head": {"sha": HEAD}}},
                        {"pull_request": None}, []):
            with self.subTest(payload=payload):
                calls = []

                def fetch(url, auth):
                    calls.append(url)
                    return {}, None
                out, _ = run_gate("pull_request", fetch=fetch, payload=payload)
                self.assertRun("true", out)
                self.assertEqual([], calls)

    def test_listing_cap_with_underreported_count(self):
        files = [entry("docs/{0}.md".format(i)) for i in range(3000)]
        pages = []
        base = pager(files)

        def fetch(url, auth):
            if "/files" in url:
                pages.append(url)
            return base(url, auth)
        def fetch_pr(url, auth):
            if url.endswith("/pulls/7"):
                return {"changed_files": 2999, "head": {"sha": HEAD}}, None
            return fetch(url, auth)
        with self.assertRaises(gate.Doubt) as caught:
            gate.list_names(API, "CodesWhat/example", 7, "secret-value", fetch_pr, HEAD)
        self.assertEqual("cap", str(caught.exception))
        self.assertEqual(30, len(pages))

    def test_pagination_loop_bound(self):
        old = gate.FILE_CAP
        gate.FILE_CAP = 4000
        self.addCleanup(setattr, gate, "FILE_CAP", old)
        files = [entry("docs/{0}.md".format(i)) for i in range(4000)]
        base = pager(files)
        pages = []

        def fetch(url, auth):
            if url.endswith("/pulls/7"):
                return {"changed_files": 3999, "head": {"sha": HEAD}}, None
            pages.append(url)
            if len(pages) > gate.MAX_PAGES + 1:
                self.fail("fetched past the page bound")
            return base(url, auth)
        with self.assertRaises(gate.Doubt) as caught:
            gate.list_names(API, "CodesWhat/example", 7, "secret-value", fetch, HEAD)
        self.assertEqual("cap", str(caught.exception))
        self.assertEqual(gate.MAX_PAGES + 1, len(pages))

    def test_range_includes_fold_kelvin_sign(self):
        out, _ = run_gate("pull_request", [entry("\u212a.txt")], patterns="[A-Z].txt")
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("\u212a.txt")], patterns="**\n![A-Z].txt")
        self.assertRun("true", out)

    def test_class_includes_fold_symmetrically(self):
        out, _ = run_gate("pull_request", [entry("\u212a.txt")], patterns="[K].txt")
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("K.txt")], patterns="[\u212a].txt")
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("docs.txt")], patterns="[K].txt")
        self.assertRun("false", out)

    def test_class_ignores_multi_character_folds(self):
        out, _ = run_gate("pull_request", [entry("\u00df.txt")], patterns="[!a-z].txt")
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("\u00df.txt")], patterns="[a-z].txt")
        self.assertRun("false", out)

    def test_class_excludes_stay_exact(self):
        out, _ = run_gate("pull_request", [entry("\u212a.txt")], patterns="**\n![K].txt")
        self.assertRun("true", out)
        out, _ = run_gate("pull_request", [entry("K.txt")], patterns="**\n![K].txt")
        self.assertRun("false", out)

    def test_head_moving_during_listing_runs(self):
        base = pager([entry("docs/a.md")])
        reads = []

        def fetch(url, auth):
            if url.endswith("/pulls/7"):
                reads.append(url)
                head = HEAD if len(reads) == 1 else "b" * 40
                return {"changed_files": 1, "head": {"sha": head}}, None
            return base(url, auth)
        out, _ = run_gate("pull_request", fetch=fetch)
        self.assertRun("true", out)
        self.assertEqual(2, len(reads))

    def test_changed_files_moving_during_listing_runs(self):
        base = pager([entry("docs/a.md")])
        reads = []

        def fetch(url, auth):
            if url.endswith("/pulls/7"):
                reads.append(url)
                return {"changed_files": len(reads), "head": {"sha": HEAD}}, None
            return base(url, auth)
        out, _ = run_gate("pull_request", fetch=fetch)
        self.assertRun("true", out)

    def test_stable_head_rechecked_and_skips(self):
        reads = []
        base = pager([entry("docs/a.md")])

        def fetch(url, auth):
            if url.endswith("/pulls/7"):
                reads.append(url)
            return base(url, auth)
        out, _ = run_gate("pull_request", fetch=fetch)
        self.assertRun("false", out)
        self.assertEqual(2, len(reads))

    def test_action_shape(self):
        action = (ACTION_DIR / "action.yml").read_text(encoding="utf-8")
        for expected in ("using: composite", "required: true",
                         "default: ${{ github.token }}",
                         'run: python3 -I "$GITHUB_ACTION_PATH/changes_gate.py"'):
            self.assertIn(expected, action)
        # Expressions are only ever passed through env, never interpolated into run.
        run_lines = [ln for ln in action.splitlines() if ln.strip().startswith("run:")]
        for line in run_lines:
            self.assertNotIn("${{", line)
        self.assertNotIn("uses:", action)


if __name__ == "__main__":
    unittest.main()
