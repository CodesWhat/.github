from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CommunityHealthContractTest(unittest.TestCase):
    def test_required_default_files_exist(self):
        required = (
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            ".github/CODEOWNERS",
            ".github/markdownlint-profile.yaml",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml",
            ".github/ISSUE_TEMPLATE/question.yml",
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/workflows/standards-validation.yml",
        )

        missing = [path for path in required if not (ROOT / path).is_file()]

        self.assertEqual([], missing, f"missing community health files: {missing}")

    def test_codeowners_has_org_catch_all(self):
        codeowners = self.read_text(".github/CODEOWNERS")

        self.assertEqual(
            "* @scttbnsn @ALARGECOMPANY @biggest-littlest\n",
            codeowners,
        )

    def test_security_policy_has_org_baseline(self):
        policy = self.read_text("SECURITY.md")

        for expected in (
            "## Supported versions",
            "## Reporting a vulnerability",
            "## Security scope",
            "Do not open a public GitHub issue",
            "security@codeswhat.com",
            "private vulnerability reporting",
            "48 hours",
            "7 days",
        ):
            self.assertIn(expected, policy)

    def test_contributor_guide_matches_org_workflow(self):
        guide = self.read_text("CONTRIBUTING.md")

        for expected in (
            "AGENTS.md",
            "active development or integration branch",
            "Conventional Commits",
            "feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert",
            "without emoji",
            "tests",
            "documentation",
            "required checks and reviews",
            "SECURITY.md",
        ):
            self.assertIn(expected, guide)

    def test_code_of_conduct_has_org_contact_and_attribution(self):
        conduct = self.read_text("CODE_OF_CONDUCT.md")

        for expected in (
            "Contributor Covenant",
            "security@codeswhat.com",
            "monitored for both security and conduct reports",
            "## Enforcement",
        ):
            self.assertIn(expected, conduct)

    def test_issue_forms_have_unique_ids_and_required_reproduction_fields(self):
        bug_report = self.read_text(".github/ISSUE_TEMPLATE/bug_report.yml")
        feature_request = self.read_text(
            ".github/ISSUE_TEMPLATE/feature_request.yml"
        )
        question = self.read_text(".github/ISSUE_TEMPLATE/question.yml")

        self.assertIn('labels: ["bug"]', bug_report)
        self.assertIn('labels: ["enhancement"]', feature_request)
        self.assertIn('labels: ["question"]', question)
        self.assert_form_ids_are_unique(bug_report)
        self.assert_form_ids_are_unique(feature_request)
        self.assert_form_ids_are_unique(question)

        required_bug_fields = {"version", "description", "expected", "reproduce"}
        actual_required = self.required_form_ids(bug_report)
        self.assertTrue(required_bug_fields <= actual_required)

        self.assertIn("security@codeswhat.com", bug_report)
        self.assertIn("redact", bug_report.lower())

    def test_issue_template_config_routes_security_reports_privately(self):
        config = self.read_text(".github/ISSUE_TEMPLATE/config.yml")

        self.assertIn("blank_issues_enabled: false", config)
        self.assertIn("url: mailto:security@codeswhat.com", config)
        self.assertIn("privately", config.lower())

    def test_pull_request_template_requires_verification_and_safe_content(self):
        template = self.read_text(".github/PULL_REQUEST_TEMPLATE.md")

        for expected in (
            "## Summary",
            "## Changes",
            "## Verification",
            "## Security and compatibility",
            "## Checklist",
            "target branch",
            "tests",
            "documentation",
            "required checks and reviews",
            "secrets, credentials, or private data",
        ):
            self.assertIn(expected, template)

    def test_standards_workflow_has_unconditional_stable_jobs(self):
        workflow = self.read_text(".github/workflows/standards-validation.yml")

        self.assertEqual(2, workflow.count("name: Standards Validation"))
        self.assertIn("    name: CodeQL\n", workflow)
        self.assertIn("  pull_request:\n", workflow)
        self.assertIn("      - main\n", workflow)
        self.assertIn("      - dev/repository-standards\n", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertIn("permissions: {}", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("group: standards-validation-", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertNotIn("matrix:", workflow)
        self.assertNotIn("continue-on-error:", workflow)
        self.assertNotIn("\n    if:", workflow)

        validation_job = workflow.split("  validation:\n", 1)[1].split(
            "\n  codeql:\n", 1
        )[0]
        self.assertIn("      actions: read  # Read workflow metadata", validation_job)
        self.assertIn("      contents: read  # Check out and validate repository files", validation_job)
        self.assertNotIn("write", validation_job)

        codeql_job = workflow.split("\n  codeql:\n", 1)[1]
        self.assertIn("      actions: read  # Read workflow metadata", codeql_job)
        self.assertIn("      contents: read  # Check out Python sources", codeql_job)
        self.assertIn("      security-events: write  # Upload CodeQL analysis", codeql_job)

        jobs = re.findall(
            r"^  ([a-z][a-z0-9-]+):$",
            workflow.split("\njobs:\n", 1)[1],
            re.MULTILINE,
        )
        self.assertEqual(["validation", "codeql"], jobs)

    def test_standards_workflow_pins_actions_and_runs_every_contract(self):
        workflow = self.read_text(".github/workflows/standards-validation.yml")
        actions = re.findall(r"^\s+uses: ([^\s#]+)", workflow, re.MULTILINE)

        self.assertTrue(actions)
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

        for expected in (
            "persist-credentials: false",
            "community_health_contract_test.py",
            "python3 -m compileall -q .",
            "YAML.parse_file",
            "json.load",
            "markdownlint-cli2@0.23.2",
            "markdownlint-profile.yaml",
            "actionlint",
            "zizmorcore/zizmor-action@",
            "github/codeql-action/init@",
            "languages: python",
            "github/codeql-action/analyze@",
        ):
            self.assertIn(expected, workflow)

    def test_zizmor_action_owns_its_supported_cli_version(self):
        workflow = self.read_text(".github/workflows/standards-validation.yml")
        zizmor_step = workflow.split(
            "      - name: Validate workflows with zizmor\n", 1
        )[1].split("\n  codeql:\n", 1)[0]

        self.assertNotIn("\n          version:", zizmor_step)

    def test_linter_configs_do_not_suppress_workflow_findings(self):
        markdown_config = self.read_text(".github/markdownlint-profile.yaml")

        self.assertIn("MD033: false", markdown_config)
        self.assertIn("MD041: false", markdown_config)
        self.assertNotIn("ignores:", markdown_config)
        self.assertEqual(2, markdown_config.count(": false"))
        self.assertFalse((ROOT / ".github/zizmor.yml").exists())

    def test_profile_generator_has_one_canonical_location(self):
        self.assertFalse((ROOT / "generate_svg.py").exists())
        self.assertTrue((ROOT / "scripts/generate_profile_svg.py").is_file())

    def test_dead_profile_cache_is_removed(self):
        self.assertFalse((ROOT / "cache").exists())

    def test_font_reference_is_local_only(self):
        gitignore = self.read_text(".gitignore")
        self.assertIn("profile/font_reference.svg", gitignore.splitlines())

    def test_profile_generator_reproduces_committed_assets(self):
        generator = ROOT / "scripts/generate_profile_svg.py"
        self.assertTrue(generator.is_file(), f"missing generator: {generator}")
        outputs = (
            ROOT / "profile/dark_mode.svg",
            ROOT / "profile/light_mode.svg",
        )
        before = {path: path.read_bytes() for path in outputs}

        subprocess.run(
            [sys.executable, generator],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        after = {path: path.read_bytes() for path in outputs}
        self.assertEqual(before, after)

    def test_profile_workflow_is_read_only_bounded_and_pinned(self):
        workflow = self.read_text(".github/workflows/profile-assets.yml")
        actions = re.findall(r"^\s+uses: ([^\s#]+)", workflow, re.MULTILINE)

        self.assertEqual(2, len(actions))
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

        harden_marker = "      - name: Harden runner\n"
        self.assertIn(harden_marker, workflow)
        harden_step = workflow.split(harden_marker, 1)[1].split("\n      - name:", 1)[0]

        self.assertIn("egress-policy: block", harden_step)
        self.assertIn("allowed-endpoints: github.com:443", harden_step)

        checkout_marker = "      - name: Check out repository\n"
        self.assertIn(checkout_marker, workflow)
        checkout_step = workflow.split(checkout_marker, 1)[1].split(
            "\n      - name:", 1
        )[0]

        self.assertIn("persist-credentials: false", checkout_step)
        self.assertIn("permissions: {}", workflow)
        self.assertIn("name: Profile Assets", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("timeout-minutes:", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("step-security/harden-runner@", workflow)
        self.assertIn("python3 scripts/generate_profile_svg.py", workflow)
        self.assertIn("git diff --exit-code -- profile/", workflow)
        self.assertIn("git status --porcelain --untracked-files=all -- profile/", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("git push", workflow)
        self.assertNotIn("GH_TOKEN", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertFalse((ROOT / ".github/workflows/update-stats.yml").exists())

    def read_text(self, relative_path):
        path = ROOT / relative_path
        self.assertTrue(path.is_file(), f"missing community health file: {relative_path}")
        return path.read_text()

    def assert_form_ids_are_unique(self, form):
        ids = re.findall(r"^    id: ([a-z][a-z0-9_-]*)$", form, re.MULTILINE)
        self.assertEqual(len(ids), len(set(ids)))

    def required_form_ids(self, form):
        required = set()
        for field in form.split("\n  - type:"):
            field_id = re.search(r"^    id: ([a-z][a-z0-9_-]*)$", field, re.MULTILINE)
            if field_id and "      required: true" in field:
                required.add(field_id.group(1))
        return required


if __name__ == "__main__":
    unittest.main()
