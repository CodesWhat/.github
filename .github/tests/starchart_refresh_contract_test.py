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
            "      accent:\n",
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
            "ACCENT: ${{ inputs.accent }}",
            "MAX_PAGES: ${{ inputs.max-pages }}",
            "TARGET_BRANCH: ${{ inputs.branch }}",
            "const repo = process.env.TARGET_REPO",
            "const out = process.env.OUTPUT_PATH",
            "const accent = process.env.ACCENT",
        ):
            self.assertIn(expected, workflow)

        generator = workflow.split("<<'GENERATOR'", 1)[1].split("GENERATOR", 1)[0]
        self.assertNotIn("${{", generator)

    def test_chart_is_self_contained_with_no_external_references(self):
        """A committed artifact that reaches out at render time would
        reintroduce exactly the silent failure this replaced."""
        workflow = self.read_workflow()

        self.assertIn("<svg xmlns=", workflow)
        self.assertIn('role="img"', workflow)

        # No media query, deliberately. GitHub's theme toggle does not reach
        # one inside an <img>-embedded SVG, so a self-theming file shows a
        # white card to anyone reading GitHub dark with a light OS. Two files
        # and a README <picture> is the mechanism that does follow the toggle.
        self.assertNotIn("prefers-color-scheme", workflow)
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

    def test_an_accent_that_is_not_a_colour_fails_rather_than_drawing_nothing(self):
        """An unset or malformed accent reaches the SVG as stroke="" and
        renders a chart with no line: a green run producing a broken image,
        which is the silent-success shape this whole workflow exists to kill.
        """
        workflow = self.read_workflow()

        self.assertIn("/^#[0-9a-fA-F]{6}$/.test(accent ?? '')", workflow)
        # Required with no default, so a caller cannot inherit someone else's
        # brand colour by forgetting to pass its own.
        accent_block = workflow.split("      accent:\n", 1)[1].split("      max-pages:", 1)[0]
        self.assertIn("required: true", accent_block)
        self.assertNotIn("default:", accent_block)

    def test_both_themes_are_written_and_committed_together(self):
        """A <picture> that gained a fresh light chart and kept a stale dark
        one shows two different histories depending on who is looking, and
        nothing reports it as wrong."""
        workflow = self.read_workflow()

        self.assertIn("writeFileSync(target, light)", workflow)
        self.assertIn("writeFileSync(darkTarget, dark)", workflow)
        self.assertIn('DARK_PATH="${OUTPUT_PATH%.svg}-dark.svg"', workflow)
        self.assertIn('git add -- "$OUTPUT_PATH" "$DARK_PATH"', workflow)
        self.assertIn('git status --porcelain -- "$OUTPUT_PATH" "$DARK_PATH"', workflow)

        # Both derivations strip a .svg suffix, so the input has to have one.
        self.assertIn("!out.endsWith('.svg')", workflow)

    def test_the_documented_trigger_is_a_dispatch_not_a_cron_or_a_release(self):
        """Two ways to get this wrong, and the second one looks right.

        A cron mutates a committed artifact underneath a tag, which 'main is
        the released version' forbids. And `release: [published]` never fires
        at all: GitHub suppresses workflow runs for events caused by
        GITHUB_TOKEN, which is what every repo here publishes releases with,
        so a caller wired that way is green everywhere and refreshes nothing.
        This file told three repos to do exactly that on 2026-08-21 before the
        sockguard lane caught it, so the example is pinned by a test now."""
        workflow = self.read_workflow()

        example = workflow.split("#   on:\n", 1)[1].split("#   permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", example)
        self.assertIn('#         accent: "#49bcfb"', workflow)
        for dead in ("release:", "types: [published]", "cron", "schedule:"):
            self.assertNotIn(dead, example)

    def test_the_suppression_trap_is_documented_not_just_avoided(self):
        """Removing the bad example only stops it being copied from here. The
        reason has to travel with it, or the next person reaches for the
        release trigger from first principles and it fails the same silent
        way."""
        workflow = self.read_workflow()

        for expected in (
            "GITHUB_TOKEN",
            "gh workflow run",
            "workflow_dispatch` and",
            "repository_dispatch",
        ):
            self.assertIn(expected, workflow)

        # The failure mode named, so it reads as a trap rather than a
        # preference: wired that way it lints clean and never runs.
        self.assertIn("refreshes nothing", workflow)

    def test_the_embedded_renderer_names_its_source(self):
        """The same renderer exists here and in ops. Hand-copying is how they
        drift, so the block is generated and says so."""
        workflow = self.read_workflow()

        self.assertIn("// BEGIN GENERATED FROM ops scripts/starchart/render-chart.mjs", workflow)
        self.assertIn("// END GENERATED", workflow)
        self.assertIn("splice-into-workflow.mjs", workflow)
        self.assertLess(
            workflow.index("// BEGIN GENERATED"),
            workflow.index("// END GENERATED"),
        )

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
