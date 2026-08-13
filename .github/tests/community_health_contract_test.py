from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CommunityHealthContractTest(unittest.TestCase):
    def test_required_default_files_exist(self):
        required = (
            "SECURITY.md",
            "CONTRIBUTING.md",
            ".github/CODEOWNERS",
            ".github/ISSUE_TEMPLATE/bug_report.yml",
            ".github/ISSUE_TEMPLATE/feature_request.yml",
            ".github/ISSUE_TEMPLATE/question.yml",
            ".github/ISSUE_TEMPLATE/config.yml",
            ".github/PULL_REQUEST_TEMPLATE.md",
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
