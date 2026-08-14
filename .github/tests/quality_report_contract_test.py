import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "quality-report" / "v1"
FIXTURES = ROOT / ".github" / "tests" / "fixtures" / "quality-report" / "v1"
SCRIPT = CONTRACT_ROOT / "quality_report.py"
SCHEMA = CONTRACT_ROOT / "schema.json"
WORKFLOW = ROOT / ".github" / "workflows" / "quality-report-aggregate.yml"
DECISION = ROOT / "decisions" / "quality-reporting.md"
SHA = "0123456789abcdef0123456789abcdef01234567"


class QualityReportContractTest(unittest.TestCase):
    def test_fixture_inventory_covers_failure_and_score_boundaries(self):
        self.assertEqual(
            {
                "canonical-score",
                "complete",
                "crash",
                "incomplete",
                "native-score",
                "parse-error",
            },
            {path.name for path in FIXTURES.iterdir() if path.is_dir()},
        )

    def test_complete_fuzz_fixture_emits_a_valid_report_and_summary(self):
        result, report, summary = self.aggregate(
            "complete",
            track="fuzz",
            tool="go-fuzz",
            expected=[
                "internal/auth/FuzzVerifyRequest",
                "internal/docker/FuzzDecodeStats",
            ],
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("quality-report/v1", report["schema_version"])
        self.assertEqual("report", report["kind"])
        self.assertEqual(
            {"expected": 2, "reported": 2, "complete": True},
            report["completeness"],
        )
        self.assertEqual("passed", report["outcome"])
        self.assertEqual(420, report["metrics"]["declared_budget_seconds"])
        self.assertEqual(399.5, report["metrics"]["elapsed_seconds"])
        self.assertEqual(1900000, report["metrics"]["executions"])
        self.assertEqual(5, report["metrics"]["new_interesting_inputs"])
        self.assertNotIn("canonical_score_pct", report["metrics"])
        self.assertIn("complete 2/2", summary)
        self.assertEqual(0, self.validate_report(report).returncode)

    def test_incomplete_fixture_fails_closed_without_a_subset_score(self):
        result, report, summary = self.aggregate(
            "incomplete",
            track="mutation",
            tool="stryker",
            expected=["apps-api-ops-1", "apps-api-ops-2"],
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(
            {"expected": 2, "reported": 1, "complete": False},
            report["completeness"],
        )
        self.assertEqual("error", report["outcome"])
        self.assertIsNone(report["metrics"])
        self.assertIn("missing target: apps-api-ops-2", report["errors"])
        self.assertIn("partial 1/2", summary)
        self.assertNotIn("Canonical score", summary)
        self.assertEqual(0, self.validate_report(report).returncode)

    def test_crash_fixture_fails_even_when_every_target_reports(self):
        result, report, summary = self.aggregate(
            "crash",
            track="fuzz",
            tool="go-fuzz",
            expected=[
                "internal/auth/FuzzVerifyRequest",
                "internal/docker/FuzzDecodeStats",
            ],
        )

        self.assertNotEqual(0, result.returncode)
        self.assertTrue(report["completeness"]["complete"])
        self.assertEqual("crashed", report["outcome"])
        self.assertIsNone(report["metrics"])
        self.assertIn("tool process exited 2", summary)
        self.assertEqual(0, self.validate_report(report).returncode)

    def test_parse_error_fixture_still_emits_an_error_report(self):
        result, report, summary = self.aggregate(
            "parse-error",
            track="mutation",
            tool="gremlins",
            expected=["./internal/config", "./internal/filter"],
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("error", report["outcome"])
        self.assertFalse(report["completeness"]["complete"])
        self.assertIsNone(report["metrics"])
        self.assertTrue(
            any("invalid JSON" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertIn("partial 1/2", summary)
        self.assertEqual(0, self.validate_report(report).returncode)

    def test_canonical_score_counts_timeout_and_no_coverage(self):
        result, report, summary = self.aggregate(
            "canonical-score",
            track="mutation",
            tool="gremlins",
            expected=["./internal/config", "./internal/filter"],
        )

        self.assertEqual(0, result.returncode, result.stderr)
        metrics = report["metrics"]
        self.assertEqual(70, metrics["killed"])
        self.assertEqual(10, metrics["timeout"])
        self.assertEqual(5, metrics["survived"])
        self.assertEqual(15, metrics["no_coverage"])
        self.assertEqual(80, metrics["detected"])
        self.assertEqual(20, metrics["missed"])
        self.assertEqual(80.0, metrics["canonical_score_pct"])
        self.assertEqual(93.33, metrics["tool_score_pct"])
        self.assertIn("Canonical score: **80.00%**", summary)

    def test_native_score_is_weighted_and_keeps_its_definition(self):
        result, report, summary = self.aggregate(
            "native-score",
            track="mutation",
            tool="stryker",
            expected=["apps-api-ops-1", "apps-api-ops-2"],
        )

        self.assertEqual(0, result.returncode, result.stderr)
        metrics = report["metrics"]
        self.assertEqual(60.0, metrics["canonical_score_pct"])
        self.assertEqual(66.67, metrics["tool_score_pct"])
        self.assertEqual(6, metrics["tool_score_numerator"])
        self.assertEqual(9, metrics["tool_score_denominator"])
        self.assertEqual(
            "detected / mutants with test coverage",
            metrics["tool_score_definition"],
        )
        self.assertIn("Native score: **66.67%**", summary)

    def test_validator_rejects_unknown_report_fields(self):
        result, report, _ = self.aggregate(
            "complete",
            track="fuzz",
            tool="go-fuzz",
            expected=[
                "internal/auth/FuzzVerifyRequest",
                "internal/docker/FuzzDecodeStats",
            ],
        )
        self.assertEqual(0, result.returncode, result.stderr)
        report["unreviewed"] = True

        validation = self.validate_report(report)

        self.assertNotEqual(0, validation.returncode)
        self.assertIn("unknown field", validation.stderr)

    def test_validator_rejects_missing_start_time_from_a_success_report(self):
        result, report, _ = self.aggregate(
            "complete",
            track="fuzz",
            tool="go-fuzz",
            expected=[
                "internal/auth/FuzzVerifyRequest",
                "internal/docker/FuzzDecodeStats",
            ],
        )
        self.assertEqual(0, result.returncode, result.stderr)
        report["run"]["started_at"] = None

        validation = self.validate_report(report)

        self.assertNotEqual(0, validation.returncode)
        self.assertIn("started_at", validation.stderr)

    def test_schema_is_versioned_and_fail_closed(self):
        with SCHEMA.open() as schema_file:
            schema = json.load(schema_file)

        self.assertEqual(
            "https://json-schema.org/draft/2020-12/schema",
            schema["$schema"],
        )
        self.assertEqual(
            "https://codeswhat.com/schemas/quality-report/v1/schema.json",
            schema["$id"],
        )
        self.assertEqual("quality-report/v1", schema["$defs"]["version"]["const"])
        self.assertNotIn("additionalProperties", schema)
        self.assertTrue(self.all_objects_are_closed(schema))

    def test_reusable_workflow_matches_the_public_contract(self):
        workflow = WORKFLOW.read_text()

        self.assertIn("  workflow_call:", workflow)
        for input_name in (
            "track",
            "tool",
            "policy",
            "expected_targets",
            "result_artifact_pattern",
            "report_name",
            "retention_days",
            "fail_on_incomplete",
        ):
            self.assertIn(f"      {input_name}:\n", workflow)
        for output_name in (
            "report_artifact_name",
            "completeness",
            "expected_targets",
            "reported_targets",
            "outcome",
            "canonical_score_pct",
        ):
            self.assertIn(f"      {output_name}:\n", workflow)

        self.assertIn("        default: quality-result-*", workflow)
        self.assertIn("        default: 90", workflow)
        self.assertIn("        default: true", workflow)
        self.assertIn("    if: always()", workflow)
        self.assertIn(
            "repository: ${{ fromJSON(toJSON(job)).workflow_repository }}", workflow
        )
        self.assertIn("ref: ${{ fromJSON(toJSON(job)).workflow_sha }}", workflow)
        self.assertIn(
            "name: quality-report-${{ inputs.track }}-${{ github.run_id }}-${{ github.run_attempt }}",
            workflow,
        )
        self.assertIn("retention-days: ${{ inputs.retention_days }}", workflow)
        self.assertIn("if: always()", workflow)

        actions = [
            line.split("uses: ", 1)[1].split()[0]
            for line in workflow.splitlines()
            if "uses: " in line
        ]
        self.assertTrue(actions)
        for action in actions:
            self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        for forbidden in ("git add", "git commit", "git push", "badges/"):
            self.assertNotIn(forbidden, workflow)

    def test_public_decision_records_fail_closed_and_no_source_badges(self):
        decision = DECISION.read_text()

        for expected in (
            "quality-report/v1",
            "target-result.json",
            "if: always()",
            "quality-report-<track>-<run_id>-<attempt>",
            "90 days",
            "partial N/M",
            "Do not commit badge JSON to `main`",
            "Native percentages are not comparable",
        ):
            self.assertIn(expected, decision)

    def test_standards_validation_runs_this_contract(self):
        workflow = (ROOT / ".github/workflows/standards-validation.yml").read_text()
        self.assertEqual(
            1,
            workflow.count("python3 .github/tests/quality_report_contract_test.py"),
        )

    def aggregate(self, fixture, *, track, tool, expected):
        self.assertTrue(SCRIPT.is_file(), f"missing aggregator: {SCRIPT}")
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree(FIXTURES / fixture, input_dir)
            for invalid_fixture in input_dir.rglob("target-result.invalid"):
                invalid_fixture.rename(invalid_fixture.with_name("target-result.json"))
            output_dir = Path(temp_dir) / "output"
            github_output = Path(temp_dir) / "github-output"
            summary_path = Path(temp_dir) / "step-summary"
            command = [
                "python3",
                str(SCRIPT),
                "aggregate",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
                "--repository",
                "CodesWhat/example",
                "--ref",
                "refs/heads/dev/example",
                "--sha",
                SHA,
                "--run-id",
                "1234567890",
                "--run-attempt",
                "2",
                "--event",
                "workflow_dispatch",
                "--run-url",
                "https://github.com/CodesWhat/example/actions/runs/1234567890",
                "--started-at",
                "2026-08-13T12:34:56Z",
                "--track",
                track,
                "--tool",
                tool,
                "--policy",
                "advisory",
                "--expected-targets",
                json.dumps(expected),
                "--report-name",
                "Fixture quality report",
                "--fail-on-incomplete",
                "true",
                "--github-output",
                str(github_output),
                "--github-step-summary",
                str(summary_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            report_path = output_dir / "report.json"
            summary_file = output_dir / "summary.md"
            self.assertTrue(report_path.is_file(), result.stderr)
            self.assertTrue(summary_file.is_file(), result.stderr)
            report = json.loads(report_path.read_text())
            summary = summary_file.read_text()
            return result, report, summary

    def validate_report(self, report):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            report_path.write_text(json.dumps(report))
            return subprocess.run(
                ["python3", str(SCRIPT), "validate", str(report_path)],
                capture_output=True,
                text=True,
            )

    def all_objects_are_closed(self, value):
        if isinstance(value, dict):
            if (
                value.get("type") == "object"
                and value.get("additionalProperties") is not False
            ):
                return False
            return all(self.all_objects_are_closed(item) for item in value.values())
        if isinstance(value, list):
            return all(self.all_objects_are_closed(item) for item in value)
        return True


if __name__ == "__main__":
    unittest.main()
