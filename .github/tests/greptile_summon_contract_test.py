from pathlib import Path
import re
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
            "    runs-on: ubuntu-24.04",
            "    timeout-minutes: 5",
            "      pull-requests: write",
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

        self.assertIn("HEAD_SHA: ${{ inputs.head_sha }}", workflow)
        self.assertIn("REVIEW_FOCUS: ${{ inputs.focus }}", workflow)
        self.assertNotIn("const headSha = '${{ inputs.head_sha }}'", workflow)
        self.assertNotIn("const focus = '${{ inputs.focus }}'", workflow)

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

    def read_workflow(self):
        self.assertTrue(WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}")
        return WORKFLOW.read_text()


if __name__ == "__main__":
    unittest.main()
