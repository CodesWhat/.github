from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/starchart-refresh.yml"


class StarchartRefreshContractTest(unittest.TestCase):
    def test_reusable_workflow_shape_and_narrow_permissions(self):
        workflow = self.read_workflow()

        for expected in (
            "  workflow_call:\n",
            "      branch:\n",
            "        required: true\n",
            "      output-path:\n",
            "        default: docs/assets/star-history.svg\n",
            "      max-pages:\n",
            "        type: number\n",
            "permissions: {}",
            "    runs-on: ubuntu-24.04",
            "    timeout-minutes: 10",
            "uses: step-security/harden-runner@",
            "egress-policy: block",
            "api.github.com:443",
            "github.com:443",
        ):
            self.assertIn(expected, workflow)

        # contents: write is the whole point of this workflow, but it must be
        # the ONLY elevated scope. A second write scope here would be a
        # commit-back job that can also move issues, releases, or packages.
        job_scopes = re.findall(r"^      (\w[\w-]*): write", workflow, re.MULTILINE)
        self.assertEqual(job_scopes, ["contents"])

    def test_generator_is_embedded_rather_than_fetched_at_run_time(self):
        """The caller pins this file by SHA. Anything resolved at run time
        escapes that pin, so the generator lives inline and the only network
        reads are GitHub's own API."""
        workflow = self.read_workflow()

        self.assertIn("node --input-type=module - <<'GENERATOR'", workflow)
        self.assertIn("application/vnd.github.star+json", workflow)
        self.assertIn("https://api.github.com/", workflow)

        # No second repository checkout, and no curl/wget/npm pulling code in.
        self.assertEqual(workflow.count("actions/checkout@"), 1)
        self.assertNotIn("repository: CodesWhat/.github", workflow)
        for forbidden in ("curl ", "wget ", "npx ", "npm install", "pip install"):
            self.assertNotIn(forbidden, workflow)

    def test_untrusted_input_is_read_from_the_environment(self):
        """Caller-controlled values reach the script as env vars, never as
        ${{ }} interpolated into a shell or JavaScript body."""
        workflow = self.read_workflow()

        for expected in (
            "TARGET_REPO: ${{ github.repository }}",
            "OUTPUT_PATH: ${{ inputs.output-path }}",
            "MAX_PAGES: ${{ inputs.max-pages }}",
            "TARGET_BRANCH: ${{ inputs.branch }}",
            "const repo = process.env.TARGET_REPO",
            "const out = process.env.OUTPUT_PATH",
        ):
            self.assertIn(expected, workflow)

        generator = workflow.split("<<'GENERATOR'", 1)[1].split("GENERATOR", 1)[0]
        self.assertNotIn("${{", generator)

    def test_chart_is_self_contained_with_no_external_references(self):
        """A committed artifact that reaches out at render time would
        reintroduce exactly the silent failure this replaced."""
        workflow = self.read_workflow()

        self.assertIn("<svg xmlns=", workflow)
        self.assertIn("prefers-color-scheme: light", workflow)
        self.assertIn('role="img"', workflow)
        for forbidden in ("<script", "xlink:href", "<foreignObject", "@import"):
            self.assertNotIn(forbidden, workflow)

        for retired_host in ("star-history.com", "warpchart.dev", "goreportcard.com"):
            self.assertNotIn(retired_host, workflow)

    def test_commit_back_is_conditional_and_never_targets_a_protected_branch(self):
        workflow = self.read_workflow()

        # --porcelain, not `git diff`, so a first run with an untracked SVG
        # still commits.
        self.assertIn('git status --porcelain -- "$OUTPUT_PATH"', workflow)
        self.assertNotIn('git diff --quiet -- "$OUTPUT_PATH"', workflow)
        self.assertIn('git push origin "HEAD:$TARGET_BRANCH"', workflow)

        # Under the strict release flow nothing pushes straight to main, so
        # the branch input must stay a caller decision with no default.
        branch_block = workflow.split("      branch:\n", 1)[1].split("      output-path:", 1)[0]
        self.assertNotIn("default:", branch_block)

        for forbidden in ("--force", "--no-verify", "git tag", "gh pr merge"):
            self.assertNotIn(forbidden, workflow)

    def test_a_default_branch_target_is_rejected_before_checkout(self):
        """Omitting a default only prevents omission. A caller can still pass
        main, and on a repo whose ruleset lets the push through that would
        commit straight to the default branch."""
        workflow = self.read_workflow()

        guard = workflow.split("Reject a protected branch", 1)[1].split("- name:", 1)[0]
        for branch in ("main", "master", "HEAD"):
            self.assertIn(branch, guard)
        self.assertIn("exit 1", guard)
        self.assertIn("${TARGET_BRANCH#refs/heads/}", guard)

        # The guard is worthless after the checkout has already happened.
        self.assertLess(
            workflow.index("Reject a protected branch"),
            workflow.index("actions/checkout@"),
        )

    def test_generator_rejects_traversal_and_a_non_positive_page_cap(self):
        """max-pages: 0 previously produced an empty star list, which took the
        'too few stars' exit and reported a clean no-op for a repository that
        actually has stars."""
        workflow = self.read_workflow()

        self.assertIn("relative(workspace, target).startsWith('..')", workflow)
        self.assertIn("isAbsolute(out)", workflow)
        self.assertIn("!Number.isInteger(maxPages) || maxPages < 1", workflow)

        # Truncation must fail rather than publish a partial history.
        self.assertIn("if (pages > maxPages)", workflow)
        self.assertNotIn("Math.min(Math.ceil(total / 100), maxPages)", workflow)
        self.assertNotIn("::warning::capping", workflow)

    def test_too_few_stars_is_a_clean_exit_not_a_failure(self):
        """A young repo having one star is a real state, not a broken build.
        Reporting red there trains people to ignore the signal."""
        workflow = self.read_workflow()

        self.assertIn("if (stars.length < 2)", workflow)
        self.assertIn("process.exit(0)", workflow)
        self.assertNotIn("process.exit(1)", workflow)
        self.assertNotIn("process.exit(2)", workflow)

    def test_embedded_generator_is_valid_javascript(self):
        """This workflow never runs in this repository, so a syntax error in
        the heredoc would first surface in a consumer's scheduled job. Parse
        it here instead."""
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not available")

        source = self.read_generator()
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "generator.mjs"
            script.write_text(source)
            result = subprocess.run(
                [node, "--check", str(script)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_workflow_pins_actions_and_is_run_by_standards_validation(self):
        workflow = self.read_workflow()
        actions = re.findall(r"^\s+uses: ([^\s#]+)", workflow, re.MULTILINE)

        self.assertTrue(actions)
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

        validation = (ROOT / ".github/workflows/standards-validation.yml").read_text()
        self.assertIn("starchart_refresh_contract_test.py", validation)

    def read_workflow(self):
        self.assertTrue(WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}")
        return WORKFLOW.read_text()

    def read_generator(self):
        """Recover the generator exactly as the shell will see it: YAML strips
        the run block's base indentation, so a heredoc body that only looks
        right in the file can still reach node malformed."""
        workflow = self.read_workflow()
        opener = "node --input-type=module - <<'GENERATOR'\n"
        self.assertIn(opener, workflow)

        indent = " " * (len(workflow.split(opener)[0].rsplit("\n", 1)[-1]))
        self.assertTrue(indent, "expected the run block to be indented")

        body = workflow.split(opener, 1)[1].split(f"\n{indent}GENERATOR", 1)[0]
        return "\n".join(
            line[len(indent):] if line.startswith(indent) else line
            for line in body.split("\n")
        )


if __name__ == "__main__":
    unittest.main()
