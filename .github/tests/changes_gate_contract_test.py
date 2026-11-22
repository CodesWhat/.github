import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


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


def pager(files):
    """Mock HTTP layer serving files in pages of 100."""
    def fetch(url, auth):
        page = int(url.rsplit("page=", 1)[1])
        return files[(page - 1) * 100:page * 100]
    return fetch


def run_gate(event, files=None, patterns=PATTERNS, fetch=None, number=7):
    with tempfile.TemporaryDirectory() as tmp:
        event_path = Path(tmp) / "event.json"
        event_path.write_text(json.dumps({"pull_request": {"number": number}}))
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
            gate.main(env, fetch or pager(files or []))
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
                out, _ = run_gate("pull_request", fetch=lambda u, a, b=bad: b)
                self.assertRun("true", out)

    def test_three_thousand_file_cap_runs(self):
        files = [entry("docs/{0}.md".format(i)) for i in range(3000)]
        out, _ = run_gate("pull_request", files)
        self.assertRun("true", out)
        files = [entry("docs/{0}.md".format(i)) for i in range(2999)]
        out, _ = run_gate("pull_request", files)
        self.assertRun("false", out)

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

    def test_action_shape(self):
        action = (ACTION_DIR / "action.yml").read_text(encoding="utf-8")
        for expected in ("using: composite", "required: true",
                         "default: ${{ github.token }}",
                         'run: python3 "$GITHUB_ACTION_PATH/changes_gate.py"'):
            self.assertIn(expected, action)
        # Expressions are only ever passed through env, never interpolated into run.
        run_lines = [l for l in action.splitlines() if l.strip().startswith("run:")]
        for line in run_lines:
            self.assertNotIn("${{", line)
        self.assertNotIn("uses:", action)


if __name__ == "__main__":
    unittest.main()
