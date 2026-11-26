from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/greptile-summon.yml"


class GreptileSummonContractTest(unittest.TestCase):
    def test_reusable_workflow_has_exact_head_inputs_and_narrow_permissions(self):
        workflow = self.read_workflow()

        for expected in (
            "  workflow_call:\n",
            "      pr_number:\n",
            "        type: number\n",
            "      head_sha:\n",
            "        type: string\n",
            "      focus:\n",
            "permissions: {}",
            "    runs-on: ubuntu-26.04",
            "    timeout-minutes: 5",
            "      pull-requests: write",
            "uses: step-security/harden-runner@",
            "egress-policy: block",
            "api.github.com:443",
            "github-token: ${{ github.token }}",
        ):
            self.assertIn(expected, workflow)

        self.assertNotIn("actions/checkout", workflow)

    def test_workflow_verifies_head_and_deduplicates_before_commenting(self):
        workflow = self.read_workflow()

        for expected in (
            "github.rest.pulls.get",
            'pull.state !== "open"',
            "pull.head.sha !== headSha",
            "github.paginate",
            "github.rest.issues.listComments",
            "greptile-summon:${headSha}",
            'comment.user?.login === "github-actions[bot]"',
            "github.rest.issues.createComment",
            "@greptileai",
        ):
            self.assertIn(expected, workflow)

        self.assertIn(
            "greptile-summon-${{ github.repository }}-${{ inputs.pr_number }}-${{ inputs.head_sha }}",
            workflow,
        )
        self.assertIn("      cancel-in-progress: false", workflow)
        self.assertGreaterEqual(workflow.count("github.rest.pulls.get"), 2)
        self.assertIn("currentPull.head.sha !== headSha", workflow)
        self.assertIn("HEAD_SHA: ${{ inputs.head_sha }}", workflow)
        self.assertIn("REVIEW_FOCUS: ${{ inputs.focus }}", workflow)
        self.assertNotIn("const headSha = '${{ inputs.head_sha }}'", workflow)
        self.assertNotIn("const focus = '${{ inputs.focus }}'", workflow)

    def test_workflow_rejects_partial_pull_request_numbers(self):
        workflow = self.read_workflow()

        self.assertIn("const prNumberText = process.env.PR_NUMBER", workflow)
        self.assertIn("const prNumber = Number(prNumberText)", workflow)
        self.assertIn("!/^[1-9]\\d*$/.test(prNumberText)", workflow)
        self.assertIn("PR_NUMBER: ${{ inputs.pr_number }}", workflow)
        self.assertIn("if (!/^[0-9a-f]{40}$/.test(headSha))", workflow)
        self.assertIn("if (focus.length === 0 || focus.length > 500)", workflow)
        self.assertNotIn("Number.parseInt(process.env.PR_NUMBER", workflow)

    def test_workflow_pins_actions_and_is_run_by_standards_validation(self):
        workflow = self.read_workflow()
        actions = re.findall(r"^\s+uses: ([^\s#]+)", workflow, re.MULTILINE)

        self.assertTrue(actions)
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

        validation = (
            ROOT / ".github/workflows/standards-validation.yml"
        ).read_text()
        self.assertIn("greptile_summon_contract_test.py", validation)

    def test_summon_script_behavior(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")

        sha = "a" * 40
        other = "b" * 40
        marker = f"<!-- greptile-summon:{sha} -->"

        def pull(head, state="open"):
            return {"data": {"state": state, "head": {"sha": head}}}

        bot_comment = {"user": {"login": "github-actions[bot]"}, "body": marker}
        cases = (
            ("abc", [], [], 1, 0, 0),
            ("1x", [], [], 1, 0, 0),
            ("7", [pull(sha, "closed")], [], 1, 0, 0),
            ("7", [pull(other)], [], 1, 0, 0),
            ("7", [pull(sha)], [bot_comment], 0, 0, 1),
            ("7", [pull(sha), pull(other)], [], 1, 0, 0),
            ("7", [pull(sha), pull(sha)], [], 0, 1, 0),
        )

        body = self.script_body(self.read_workflow())
        harness = textwrap.dedent(
            """\
            const body = %s;
            const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
            const run = new AsyncFunction("github", "core", "context", "process", body);
            const scenario = JSON.parse(process.argv[2]);
            const calls = { setFailed: [], notice: [], createComment: [] };
            const queue = [...scenario.pulls];
            const listComments = () => {};
            const github = {
              rest: {
                pulls: { get: async () => queue.shift() },
                issues: {
                  listComments,
                  createComment: async (args) => { calls.createComment.push(args); },
                },
              },
              paginate: async () => scenario.comments,
            };
            const core = {
              setFailed: (m) => calls.setFailed.push(m),
              notice: (m) => calls.notice.push(m),
            };
            const context = { repo: { owner: "o", repo: "r" } };
            run(github, core, context, process).then(
              () => console.log(JSON.stringify(calls)),
              (e) => { console.error(e); process.exit(1); },
            );
            """
        ) % json.dumps(body)

        with tempfile.TemporaryDirectory() as tmp:
            harness_path = Path(tmp) / "harness.js"
            harness_path.write_text(harness)
            for pr_number, pulls, comments, failed, created, notices in cases:
                with self.subTest(pr_number=pr_number, pulls=pulls):
                    env = {
                        **os.environ,
                        "PR_NUMBER": pr_number,
                        "HEAD_SHA": sha,
                        "REVIEW_FOCUS": "check the diff",
                    }
                    result = subprocess.run(
                        [node, str(harness_path), json.dumps({"pulls": pulls, "comments": comments})],
                        check=True,
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    calls = json.loads(result.stdout)
                    self.assertEqual(len(calls["setFailed"]), failed, calls)
                    self.assertEqual(len(calls["createComment"]), created, calls)
                    self.assertEqual(len(calls["notice"]), notices, calls)
                    if created:
                        comment_body = calls["createComment"][0]["body"]
                        self.assertIn("@greptileai", comment_body)
                        self.assertIn(marker, comment_body)

    def script_body(self, workflow):
        lines = workflow.split("          script: |\n", 1)[1].splitlines()
        body = []
        for line in lines:
            if line.strip() and not line.startswith(" " * 12):
                break
            body.append(line[12:])
        return "\n".join(body)

    def read_workflow(self):
        self.assertTrue(WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}")
        return WORKFLOW.read_text()


if __name__ == "__main__":
    unittest.main()
