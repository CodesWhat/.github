from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/main-is-released.yml"


class MainIsReleasedContractTest(unittest.TestCase):
    def test_reusable_workflow_shape_and_read_only_permissions(self):
        workflow = self.read_workflow()

        for expected in (
            "  workflow_call:\n",
            "permissions: {}",
            "    runs-on: ubuntu-24.04",
            "uses: step-security/harden-runner@",
            "egress-policy: block",
            "github.com:443",
            "      contents: read",
        ):
            self.assertIn(expected, workflow)

        # This check only ever reads. Any write scope here would be a
        # scheduled job with credentials to change the thing it audits.
        self.assertNotIn(": write", workflow)
        self.assertNotIn("persist-credentials: true", workflow)

    def test_the_invariant_is_an_exact_tag_match(self):
        workflow = self.read_workflow()

        self.assertIn("git describe --exact-match --tags HEAD", workflow)
        # --abbrev=0 alone answers "what tag is nearest", which is true of a
        # drifted main too. It may only be used to report inside the failure
        # branch, never in the condition that decides pass or fail — so the
        # decisive slice is the condition line, not the whole if-block.
        decisive = workflow.split('if tag="$(', 1)[1].split("\n", 1)[0]
        self.assertIn("--exact-match", decisive)
        self.assertNotIn("--abbrev=0", decisive)

    def test_no_tags_is_reported_as_unevaluable_not_as_drift(self):
        """A repo with zero tags produces the same 'not tagged' as one that
        drifted. Those need different answers, so the measurement proves it
        could have worked before its result is trusted."""
        workflow = self.read_workflow()

        self.assertIn('if [ -z "$(git tag)" ]; then', workflow)
        self.assertLess(
            workflow.index('if [ -z "$(git tag)" ]'),
            workflow.index("git describe --exact-match"),
        )

    def test_shallow_checkout_would_break_the_measurement(self):
        """describe needs tags and history; a shallow clone fails for the
        wrong reason and reads as real drift."""
        workflow = self.read_workflow()
        self.assertIn("fetch-depth: 0", workflow)

    def test_an_exact_match_alone_is_not_the_invariant(self):
        """Any tag satisfies --exact-match, including a moving or descriptive
        one. A tag literally named `snapshot` parked on a drifted main reads
        as a clean pass, which is the exact failure this workflow exists to
        catch. Found by the sockguard lane, 2026-08-21."""
        workflow = self.read_workflow()

        self.assertIn("[0-9]+\\.[0-9]+\\.[0-9]+", workflow)
        self.assertIn("not a release version tag", workflow)

        # Prerelease detection keys on the hyphen AFTER the version, not any
        # hyphen anywhere — the old `case $tag in *-*)` called `my-tag` a
        # prerelease and would have accepted it under allow-prerelease.
        self.assertNotIn('case "$tag" in', workflow)
        self.assertIn("^v?[0-9]+\\.[0-9]+\\.[0-9]+-", workflow)

    def test_the_merge_to_tag_window_is_retried_not_reported_as_drift(self):
        """A promotion merges before its tag is pushed. A run landing in that
        window sees an untagged main and reports drift that resolves itself
        seconds later, which trains people to ignore the one check whose job
        is being noticed."""
        workflow = self.read_workflow()

        self.assertIn("for attempt in 1 2 3", workflow)
        self.assertIn("git fetch --tags --force", workflow)
        self.assertIn("sleep 20", workflow)

        # The retry must not become a way to pass. A failed refetch is warned
        # about and the loop still decides on the refs it has.
        self.assertIn("::warning::could not refetch tags", workflow)
        self.assertNotIn("exit 0", workflow)

    def test_a_prerelease_on_main_fails_by_default(self):
        """A release candidate on the default branch is the exact drift this
        exists to catch: drydock's main sat on v1.7.0-rc.2."""
        workflow = self.read_workflow()

        self.assertIn("        default: false\n", workflow)
        self.assertIn('if [ "$ALLOW_PRERELEASE" != "true" ]', workflow)
        self.assertIn("ALLOW_PRERELEASE: ${{ inputs.allow-prerelease }}", workflow)

    def test_failures_say_what_to_do_next(self):
        workflow = self.read_workflow()

        for expected in ("::error::", "cut a release", "dev branch"):
            self.assertIn(expected, workflow)
        self.assertIn("set -euo pipefail", workflow)

    def test_workflow_pins_actions_and_is_run_by_standards_validation(self):
        workflow = self.read_workflow()
        actions = re.findall(r"^\s+uses: ([^\s#]+)", workflow, re.MULTILINE)

        self.assertTrue(actions)
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

        validation = (ROOT / ".github/workflows/standards-validation.yml").read_text()
        self.assertIn("main_is_released_contract_test.py", validation)

    def read_workflow(self):
        self.assertTrue(WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}")
        return WORKFLOW.read_text()


if __name__ == "__main__":
    unittest.main()
