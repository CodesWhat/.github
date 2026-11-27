import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "quality-report" / "v1"
FIXTURES = ROOT / ".github" / "tests" / "fixtures" / "quality-report" / "v1"
SCRIPT = CONTRACT_ROOT / "quality_report.py"
SCHEMA = CONTRACT_ROOT / "schema.json"
WORKFLOW = ROOT / ".github" / "workflows" / "quality-report-aggregate.yml"
DECISION = ROOT / "decisions" / "quality-reporting.md"
SHA = "0123456789abcdef0123456789abcdef01234567"
SELECTOR = CONTRACT_ROOT / "select_artifacts.py"
QUALITY_REPORT = runpy.run_path(str(SCRIPT))
SELECTOR_MODULE = runpy.run_path(str(SELECTOR))


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
        self.assertEqual(420.0, report["metrics"]["elapsed_seconds"])
        self.assertEqual(1900000, report["metrics"]["executions"])
        self.assertEqual(5, report["metrics"]["new_interesting_inputs"])
        self.assertNotIn("canonical_score_pct", report["metrics"])
        self.assertIn("complete 2/2", summary)
        self.assertEqual(0, self.validate_report(report).returncode)

    def test_aggregate_rejects_fail_on_incomplete_false(self):
        result, report, _summary = self.aggregate(
            "complete",
            track="fuzz",
            tool="go-fuzz",
            expected=[
                "internal/auth/FuzzVerifyRequest",
                "internal/docker/FuzzDecodeStats",
            ],
            fail_on_incomplete="false",
        )

        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn(
            "fail_on_incomplete must be true for CodesWhat telemetry",
            report["errors"],
        )
        self.assertNotEqual("passed", report["outcome"])

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

    def test_partial_rerun_without_a_replacement_fails_closed(self):
        def drop_stale_target(input_dir):
            shutil.rmtree(input_dir / "quality-result-stats")

        result, report, summary = self.aggregate(
            "complete",
            track="fuzz",
            tool="go-fuzz",
            expected=[
                "internal/auth/FuzzVerifyRequest",
                "internal/docker/FuzzDecodeStats",
            ],
            input_mutator=drop_stale_target,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertFalse(report["completeness"]["complete"])
        self.assertEqual("error", report["outcome"])
        self.assertIsNone(report["metrics"])
        self.assertNotIn("Canonical score", summary)

    def selector_fetch(self, artifacts, started_at="2026-08-13T12:00:00Z"):
        calls = []

        def fetch(path):
            calls.append(path)
            if "/attempts/" in path:
                return {"run_started_at": started_at}
            page = int(path.rsplit("page=", 1)[1])
            size = SELECTOR_MODULE["PAGE_SIZE"]
            return {
                "total_count": len(artifacts),
                "artifacts": artifacts[(page - 1) * size : page * size],
            }

        return fetch, calls

    def artifact(self, artifact_id, created_at, name="quality-result-a"):
        return {"id": artifact_id, "name": name, "created_at": created_at}

    def select(self, artifacts, pattern="quality-result-*", **kwargs):
        fetch, calls = self.selector_fetch(artifacts, **kwargs)
        ids = SELECTOR_MODULE["select_artifacts"](
            "CodesWhat/example", "1", "2", pattern, fetch
        )
        return ids, calls

    def test_selector_keeps_only_fresh_artifacts_matching_the_pattern(self):
        ids, calls = self.select(
            [
                self.artifact(1, "2026-08-13T11:59:59Z"),
                self.artifact(2, "2026-08-13T12:00:01Z"),
                self.artifact(3, "2026-08-13T12:05:00Z", name="other-result"),
                self.artifact(4, "2026-08-13T12:10:00Z", name="quality-result-b"),
            ]
        )

        self.assertEqual([2, 4], ids)
        self.assertIn("/repos/CodesWhat/example/actions/runs/1/attempts/2", calls[0])

    def test_selector_expands_brace_alternatives_like_download_artifact(self):
        ids, _ = self.select(
            [
                self.artifact(1, "2026-08-13T12:01:00Z", name="quality-result-auth"),
                self.artifact(2, "2026-08-13T12:01:00Z", name="quality-result-stats"),
                self.artifact(3, "2026-08-13T12:01:00Z", name="quality-result-other"),
                self.artifact(4, "2026-08-13T11:00:00Z", name="quality-result-auth"),
            ],
            pattern="quality-result-{auth,stats}",
        )

        self.assertEqual([1, 2], ids)

    def test_selector_selects_extglob_alternatives(self):
        ids, _ = self.select(
            [
                self.artifact(1, "2026-08-13T12:01:00Z", name="quality-result-auth"),
                self.artifact(2, "2026-08-13T12:01:00Z", name="quality-result-stats"),
                self.artifact(3, "2026-08-13T12:01:00Z", name="quality-result-other"),
            ],
            pattern="quality-result-@(auth|stats)",
        )

        self.assertEqual([1, 2], ids)

    def test_matches_agrees_with_minimatch_defaults(self):
        # Each row was checked against minimatch 10 with default options, the
        # matcher actions/download-artifact applies to its pattern input.
        rows = [
            ("quality-result-*", "quality-result-auth", True),
            ("quality-result-*", "quality-result-", True),
            ("quality-result-*", "quality-report-auth", False),
            ("quality-result-{auth,stats}", "quality-result-stats", True),
            ("quality-result-{auth,stats}", "quality-result-other", False),
            ("result-{1..3}", "result-2", True),
            ("result-{1..3}", "result-4", False),
            ("{a,{b,c}}", "c", True),
            ("a{b", "a{b", True),
            ("quality-result-@(auth|stats)", "quality-result-auth", True),
            ("quality-result-@(auth|stats)", "quality-result-other", False),
            ("@(a|b)c", "bc", True),
            ("@(a|b)c", "abc", False),
            ("?(a|b)c", "c", True),
            ("?(a|b)c", "abc", False),
            ("+(x)", "xxx", True),
            ("+(x)", "", False),
            ("*(a|b)c", "ababc", True),
            ("*(a|b)c", "abcd", False),
            ("target-result-!(windows)-*", "target-result-linux-x64", True),
            ("target-result-!(windows)-*", "target-result-windows-x64", False),
            # a leading ! negates the whole pattern, so !(...) at the start is
            # not an extglob: it negates the literal "(auth)*"
            ("!(auth)*", "stats", True),
            ("!(auth)*", "authz", True),
            ("!(a)", "a", True),
            ("!!(a)", "(a)", True),
            ("!a", "b", True),
            ("!a", "a", False),
            ("!!a", "a", True),
            ("#a", "#a", False),
            ("", "", True),
            ("", "a", False),
            ("*", ".hidden", False),
            ("*", "visible", True),
            (".*", ".hidden", True),
            ("?", ".", False),
            ("[a-c]x", "bx", True),
            ("[a-c]x", ".x", False),
            ("@(.a|b)", ".a", True),
            ("*(a|b)", ".a", False),
            ("+(*|.a)", ".a", True),
            ("[abc]", "b", True),
            ("[a-c]", "d", False),
            ("[!a]", "b", True),
            ("[^a]", "a", False),
            ("[a", "[a", True),
            ("[]a]", "]", True),
            ("[[:alpha:]]", "q", True),
            ("[[:alpha:]]", "7", False),
            ("[[:digit:]]x", "7x", True),
            ("a\\*", "a*", True),
            ("a\\*", "ab", False),
            ("@(a|b", "@(a|b", True),
            ("@()", "@()", True),
            ("quality-report-.dist", "quality-report-.dist", True),
            ("quality-report-*", "quality-report-.dist", True),
            ("*.txt", "a.txt", True),
            ("*.txt", ".txt", False),
            ("Quality-*", "quality-a", False),
        ]
        matches = SELECTOR_MODULE["matches"]
        for pattern, name, expected in rows:
            with self.subTest(pattern=pattern, name=name):
                self.assertIs(expected, matches(name, pattern))

    def test_selector_refuses_constructs_it_cannot_mirror(self):
        error = SELECTOR_MODULE["SelectionError"]
        for pattern in ("**/quality-result-a", "quality-result-a/**", "a\\|b", "a-[[:alpha:]]"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(error):
                    self.select(
                        [self.artifact(1, "2026-08-13T12:01:00Z", name="quality-result-a")],
                        pattern=pattern,
                    )

    def refuses(self, pattern):
        error = SELECTOR_MODULE["SelectionError"]
        with self.assertRaises(error):
            self.select(
                [self.artifact(1, "2026-08-13T12:01:00Z", name="quality-result-a")],
                pattern=pattern,
            )

    def test_oversized_brace_range_bound_is_a_selection_error(self):
        self.refuses("a-{1.." + "9" * 4301 + "}")
        self.refuses("a-{" + "9" * 4301 + "..1}")
        self.assertEqual(["a-1", "a-2", "a-3"], SELECTOR_MODULE["expand_braces"]("a-{1..3}"))

    def test_brace_expansion_deduplicates_a_large_range_quickly(self):
        expanded = SELECTOR_MODULE["expand_braces"]("a-{0..99999}")
        self.assertEqual(100000, len(expanded))
        self.assertEqual("a-0", expanded[0])
        self.assertEqual(["a-1", "a-2"], SELECTOR_MODULE["expand_braces"]("a-{1,2,1}"))

    def test_deeply_nested_extglob_is_a_selection_error(self):
        self.refuses("@(" * 2000 + "a" + ")" * 2000)
        self.refuses("@(" * 65 + "a" + ")" * 65)
        self.assertTrue(SELECTOR_MODULE["matches"]("a", "@(@(a))"))

    def test_many_negation_groups_is_a_selection_error(self):
        started = time.monotonic()
        self.refuses("quality-result-" + "!(x)" * 14)
        self.refuses("quality-result-" + "!(x)" * 25)
        self.refuses("a" + "!(x|y|z)" * 8)
        self.assertLess(time.monotonic() - started, 2)

    def test_negation_copies_of_a_long_literal_are_a_selection_error(self):
        started = time.monotonic()
        self.refuses("quality-result-" + "!(x)" * 10 + "a" * 60000)
        matches = SELECTOR_MODULE["matches"]
        self.assertTrue(matches("a" * 60000, "*" + "a" * 59999))
        self.assertLess(time.monotonic() - started, 2)

    def test_ordinary_negation_groups_still_match(self):
        matches = SELECTOR_MODULE["matches"]
        started = time.monotonic()
        self.assertTrue(matches("ab", "!(a)!(b)"))
        self.assertTrue(matches("xy", "!(a)!(b)"))
        self.assertTrue(matches("quality-result-y", "quality-result-!(x)"))
        self.assertFalse(matches("quality-result-x", "quality-result-!(x)"))
        self.assertTrue(matches("pre-q-r", "pre-!(a)-!(b)"))
        self.assertFalse(matches("pre-a-r", "pre-!(a)-!(a)"))
        eight = "q-" + "!(x)" * 8
        self.assertTrue(matches("q-yyyyyyyy", eight))
        self.assertTrue(matches("q-y", eight))
        self.assertLess(time.monotonic() - started, 2)

    def test_escaped_pipe_is_refused_before_suffix_shortcuts(self):
        self.refuses("*a\\|b")
        self.refuses("??\\|b")
        self.assertTrue(SELECTOR_MODULE["matches"]("xa", "*a"))
        self.assertTrue(SELECTOR_MODULE["matches"]("b", "@(a|b)"))

    def test_brace_expansion_cap_never_drops_alternatives_silently(self):
        self.refuses("!{quality-result-{0..99999},a/../b}")
        self.refuses("!{quality-result-{0..1},a/../b}")
        self.refuses("{0..100000}")
        self.refuses("{0..99999}{a,b}")

    def test_brace_expansion_handles_nesting_and_literals(self):
        expand = SELECTOR_MODULE["expand_braces"]
        self.assertEqual(["a-x-1", "a-x-2", "a-y"], expand("a-{x-{1,2},y}"))
        self.assertEqual(["a-{x}"], expand("a-{x}"))
        self.assertEqual(["a-{x,y"], expand("a-{x,y"))
        self.assertEqual(["quality-result-*"], expand("quality-result-*"))

    def test_selector_rejects_an_artifact_created_at_the_attempt_start(self):
        ids, _ = self.select([self.artifact(1, "2026-08-13T12:00:00Z")])

        self.assertEqual([], ids)

    def test_selector_reads_every_artifact_page(self):
        artifacts = [
            self.artifact(i, "2026-08-13T13:00:00Z", name="quality-result-{0}".format(i))
            for i in range(1, 251)
        ]
        ids, calls = self.select(artifacts)

        self.assertEqual(list(range(1, 251)), ids)
        self.assertEqual(3, len([c for c in calls if "/artifacts?" in c]))

    def test_selector_rejects_missing_or_ambiguous_timestamps(self):
        error = SELECTOR_MODULE["SelectionError"]
        with self.assertRaises(error):
            self.select([{"id": 1, "name": "quality-result-a"}])
        with self.assertRaises(error):
            self.select([self.artifact(1, "2026-08-13T12:30:00")])
        with self.assertRaises(error):
            self.select([self.artifact(1, "2026-08-13T12:30:00Z")], started_at=None)

    def test_empty_selection_exits_zero_and_never_downloads_everything(self):
        fetch, _ = self.selector_fetch([self.artifact(1, "2026-08-13T11:00:00Z")])
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "github-output"
            code = SELECTOR_MODULE["main"](
                [
                    "--repository",
                    "CodesWhat/example",
                    "--run-id",
                    "1",
                    "--run-attempt",
                    "2",
                    "--pattern",
                    "quality-result-*",
                    "--github-output",
                    str(output),
                ],
                fetch,
            )
            self.assertEqual(0, code)
            self.assertEqual("artifact_ids=\n", output.read_text())

        workflow = WORKFLOW.read_text()
        self.assertIn("artifact-ids: ${{ steps.select.outputs.artifact_ids }}", workflow)
        self.assertIn("steps.select.outputs.artifact_ids != ''", workflow)
        self.assertNotIn("pattern: ${{ inputs.result_artifact_pattern }}", workflow)

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

    def test_crash_fixture_uses_a_native_go_crasher_path(self):
        result = json.loads(
            (
                FIXTURES
                / "crash"
                / "quality-result-stats"
                / "target-result.json"
            ).read_text()
        )

        self.assertEqual(
            {
                "path": "internal/docker/testdata/fuzz/FuzzDecodeStats/6f2c1b8d9a4e5c37"
            },
            result["target"]["reproduction"],
        )

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

    def test_non_utf8_target_still_emits_an_error_report(self):
        def corrupt_target(input_dir):
            target = next(input_dir.rglob("target-result.json"))
            target.write_bytes(b"\xff")

        result, report, summary = self.aggregate(
            "complete",
            track="fuzz",
            tool="go-fuzz",
            expected=[
                "internal/auth/FuzzVerifyRequest",
                "internal/docker/FuzzDecodeStats",
            ],
            input_mutator=corrupt_target,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("error", report["outcome"])
        self.assertFalse(report["completeness"]["complete"])
        self.assertIsNone(report["metrics"])
        self.assertTrue(
            any("cannot read JSON" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertIn("partial 1/2", summary)
        self.assertEqual(0, self.validate_report(report).returncode)

    def test_utf8_target_load_does_not_depend_on_the_locale_encoding(self):
        fixture = (
            FIXTURES
            / "complete"
            / "quality-result-stats"
            / "target-result.json"
        )
        original_read_text = Path.read_text
        with mock.patch.object(
            Path, "read_text", autospec=True, side_effect=original_read_text
        ) as fixture_read:
            document = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertEqual(
            mock.call(fixture, encoding="utf-8"), fixture_read.call_args
        )
        document["target"]["name"] = "internal/docker/FuzzDécodage"

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "target-result.json"
            path.write_bytes(json.dumps(document, ensure_ascii=False).encode("utf-8"))

            def read_with_ascii_default(*, encoding=None, errors=None):
                return path.read_bytes().decode(
                    encoding or "ascii", errors or "strict"
                )

            with mock.patch.object(
                Path, "read_text", side_effect=read_with_ascii_default
            ):
                loaded = QUALITY_REPORT["load_json_file"](path)

        self.assertEqual(
            "internal/docker/FuzzDécodage", loaded["target"]["name"]
        )

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

    def test_decision_defines_canonical_score_as_a_percentage(self):
        decision = DECISION.read_text()

        self.assertIn(
            "canonical_score_pct = 100 * detected / (detected + missed)",
            decision,
        )

    def test_decision_documents_track_and_tool_pairings(self):
        decision = " ".join(DECISION.read_text().split())

        self.assertIn("`mutation` permits `gremlins` and `stryker`", decision)
        self.assertIn("`fuzz` permits `fast-check` and `go-fuzz`", decision)

    def test_decision_defines_go_fuzz_as_the_native_go_engine(self):
        decision = " ".join(DECISION.read_text().split())

        self.assertIn(
            "`go-fuzz` identifies Go's native `go test -fuzz` engine, not the legacy third-party go-fuzz tool.",
            decision,
        )

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

    def test_validator_rejects_target_names_beyond_the_schema_limit(self):
        target = json.loads(
            (
                FIXTURES
                / "complete"
                / "quality-result-stats"
                / "target-result.json"
            ).read_text()
        )
        target["target"]["name"] = "x" * 257

        with self.assertRaisesRegex(QUALITY_REPORT["ContractError"], "256"):
            QUALITY_REPORT["validate_target_document"](target)
        with self.assertRaisesRegex(QUALITY_REPORT["ContractError"], "256"):
            QUALITY_REPORT["parse_expected_targets"](
                json.dumps([target["target"]["name"]])
            )

    def test_validator_requires_a_positive_declared_fuzz_budget(self):
        metrics = {
            "declared_budget_seconds": 0,
            "elapsed_seconds": 0,
            "executions": 0,
            "new_interesting_inputs": 0,
        }

        with self.assertRaisesRegex(QUALITY_REPORT["ContractError"], ">= 1"):
            QUALITY_REPORT["validate_fuzz_report_metrics"](metrics, [], "$.metrics")

    def test_passed_fuzz_target_requires_its_full_declared_budget(self):
        target = {
            "name": "internal/auth/FuzzVerifyRequest",
            "outcome": "passed",
            "metrics": {
                "budget_seconds": 300,
                "elapsed_seconds": 299.99,
            },
        }

        with self.assertRaisesRegex(
            QUALITY_REPORT["ContractError"], "completed fuzz budget"
        ):
            QUALITY_REPORT["validate_target"](target, "fuzz", "$.target")

    def test_overflowing_elapsed_seconds_is_a_contract_error(self):
        target = {
            "name": "internal/docker/FuzzDecodeStats",
            "outcome": "passed",
            "metrics": {"budget_seconds": 300, "elapsed_seconds": 10**400},
        }

        with self.assertRaisesRegex(
            QUALITY_REPORT["ContractError"], "elapsed_seconds"
        ):
            QUALITY_REPORT["validate_target"](target, "fuzz", "$.target")

    def test_oversized_integer_json_is_a_contract_error(self):
        original_limit = sys.get_int_max_str_digits()
        self.addCleanup(sys.set_int_max_str_digits, original_limit)
        sys.set_int_max_str_digits(4300)

        with self.assertRaisesRegex(QUALITY_REPORT["ContractError"], "invalid JSON"):
            QUALITY_REPORT["load_json_text"]('{"a":' + "1" * 5000 + "}", "oversized")

    def test_oversized_integer_target_still_emits_an_error_report(self):
        def oversize_target(input_dir):
            target = next(input_dir.rglob("target-result.json"))
            target.write_text('{"a":' + "1" * 5000 + "}", encoding="utf-8")

        result, report, summary = self.aggregate(
            "complete",
            track="fuzz",
            tool="go-fuzz",
            expected=[
                "internal/auth/FuzzVerifyRequest",
                "internal/docker/FuzzDecodeStats",
            ],
            input_mutator=oversize_target,
            env={**os.environ, "PYTHONINTMAXSTRDIGITS": "4300"},
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("error", report["outcome"])
        self.assertTrue(
            any("invalid JSON" in error for error in report["errors"]),
            report["errors"],
        )
        self.assertEqual(0, self.validate_report(report).returncode)

    def test_schema_requires_seed_and_path_for_fast_check_failures(self):
        definitions = json.loads(SCHEMA.read_text())["$defs"]
        failed = {
            "if": {
                "properties": {"outcome": {"enum": ["failed", "crashed", "flaked"]}},
                "required": ["outcome"],
            },
            "then": {"properties": {"reproduction": {"required": ["seed", "path"]}}},
        }

        def fast_check(properties):
            return {
                "if": {
                    "properties": {"tool": {"const": "fast-check"}},
                    "required": ["tool"],
                },
                "then": {"properties": properties},
            }

        self.assertIn(
            fast_check({"target": failed}), definitions["targetResult"]["allOf"]
        )
        self.assertIn(
            fast_check({"targets": {"items": failed}}),
            definitions["report"]["allOf"],
        )
        self.assertEqual(
            {"seed", "path", "corpus"},
            set(definitions["reproduction"]["properties"]),
        )
        self.assertEqual(1, definitions["reproduction"]["minProperties"])

    def test_fast_check_failures_require_seed_and_path(self):
        def target(reproduction):
            return {
                "name": "src/fuzz/decode",
                "outcome": "failed",
                "metrics": None,
                "diagnostic": "property failed",
                "reproduction": reproduction,
            }

        for reproduction in ({"path": "[0]"}, {"seed": "42"}):
            with self.subTest(reproduction=reproduction):
                with self.assertRaisesRegex(
                    QUALITY_REPORT["ContractError"],
                    r"\$\.target\.reproduction: fast-check failures require seed and path",
                ):
                    QUALITY_REPORT["validate_target"](
                        target(reproduction), "fuzz", "$.target", "fast-check"
                    )

        QUALITY_REPORT["validate_target"](
            target({"seed": "42", "path": "[0]"}), "fuzz", "$.target", "fast-check"
        )
        QUALITY_REPORT["validate_target"](
            target({"corpus": "corpus/crash-1"}), "fuzz", "$.target", "go-fuzz"
        )

    def test_validator_requires_time_and_offset_in_timestamps(self):
        for timestamp in ("2026-08-14", "2026-08-14T15:00:00"):
            with self.subTest(timestamp=timestamp):
                with self.assertRaisesRegex(
                    QUALITY_REPORT["ContractError"], "RFC 3339"
                ):
                    QUALITY_REPORT["require_timestamp"](timestamp, "$.run.started_at")

    def test_invalid_json_contract_error_retains_its_cause(self):
        with self.assertRaises(QUALITY_REPORT["ContractError"]) as raised:
            QUALITY_REPORT["load_json_text"]("{", "target-result.json")

        self.assertIsInstance(raised.exception.__cause__, json.JSONDecodeError)

    def test_invalid_timestamp_contract_error_retains_its_cause(self):
        with self.assertRaises(QUALITY_REPORT["ContractError"]) as raised:
            QUALITY_REPORT["require_timestamp"]("2026-08-14", "$.run.started_at")

        self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_json_parser_rejects_duplicate_fields_and_non_finite_numbers(self):
        cases = (
            ('{"value": 1, "value": 2}', "duplicate JSON field"),
            ('{"value": NaN}', "non-finite JSON number"),
            ('{"value": Infinity}', "non-finite JSON number"),
        )

        for document, message in cases:
            with self.subTest(document=document):
                with self.assertRaisesRegex(QUALITY_REPORT["ContractError"], message):
                    QUALITY_REPORT["load_json_text"](document, "target-result.json")

    def test_mutation_aggregate_rejects_mixed_native_score_definitions(self):
        targets = []
        for definition in ("detected / covered", "killed / lived"):
            targets.append(
                {
                    "metrics": {
                        "killed": 1,
                        "timeout": 0,
                        "survived": 0,
                        "no_coverage": 0,
                        "invalid": 0,
                        "ignored": 0,
                        "tool_score_numerator": 1,
                        "tool_score_denominator": 1,
                        "tool_score_pct": 100.0,
                        "tool_score_definition": definition,
                    }
                }
            )

        with self.assertRaisesRegex(
            QUALITY_REPORT["ContractError"],
            "inconsistent native score definitions",
        ):
            QUALITY_REPORT["aggregate_mutation_metrics"](targets)

    def test_target_collection_rejects_unexpected_and_duplicate_names(self):
        document = json.loads(
            (
                FIXTURES
                / "complete"
                / "quality-result-stats"
                / "target-result.json"
            ).read_text()
        )
        target_name = document["target"]["name"]

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            (input_dir / "unexpected").mkdir()
            (input_dir / "unexpected" / "target-result.json").write_text(
                json.dumps(document)
            )
            targets, errors = QUALITY_REPORT["collect_targets"](
                input_dir, ["expected-target"], "fuzz", "go-fuzz"
            )
            self.assertEqual([], targets)
            self.assertTrue(any("unexpected target" in error for error in errors))

        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            for directory in ("first", "second"):
                path = input_dir / directory
                path.mkdir()
                (path / "target-result.json").write_text(json.dumps(document))
            targets, errors = QUALITY_REPORT["collect_targets"](
                input_dir, [target_name], "fuzz", "go-fuzz"
            )
            self.assertEqual([target_name], [target["name"] for target in targets])
            self.assertTrue(any("duplicate target" in error for error in errors))

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

    def test_schema_encodes_python_target_and_track_guards(self):
        schema = json.loads(SCHEMA.read_text())
        definitions = schema["$defs"]

        self.assertEqual(
            [
                {
                    "if": {
                        "properties": {"outcome": {"const": "passed"}},
                        "required": ["outcome"],
                    },
                    "then": {
                        "properties": {"metrics": {"not": {"type": "null"}}},
                        "not": {
                            "anyOf": [
                                {"required": ["diagnostic"]},
                                {"required": ["reproduction"]},
                            ]
                        },
                    },
                    "else": {"required": ["diagnostic"]},
                }
            ],
            definitions["target"]["allOf"],
        )
        self.assertEqual(
            {"gremlins", "stryker"},
            set(
                definitions["targetResult"]["allOf"][0]["then"]["properties"][
                    "tool"
                ]["enum"]
            ),
        )
        self.assertEqual(
            {"fast-check", "go-fuzz"},
            set(
                definitions["targetResult"]["allOf"][0]["else"]["properties"][
                    "tool"
                ]["enum"]
            ),
        )
        self.assertEqual(
            "#/$defs/mutationTargetMetrics",
            definitions["mutationTarget"]["allOf"][1]["properties"]["metrics"][
                "oneOf"
            ][0]["$ref"],
        )
        self.assertEqual(
            "#/$defs/fuzzTargetMetrics",
            definitions["fuzzTarget"]["allOf"][1]["properties"]["metrics"][
                "oneOf"
            ][0]["$ref"],
        )
        self.assertEqual(
            ["reproduction"],
            definitions["fuzzTarget"]["allOf"][2]["then"]["required"],
        )
        self.assertEqual(
            "#/$defs/mutationTarget",
            definitions["report"]["allOf"][0]["then"]["properties"]["targets"][
                "items"
            ]["$ref"],
        )
        self.assertEqual(
            "#/$defs/fuzzTarget",
            definitions["report"]["allOf"][0]["else"]["properties"]["targets"][
                "items"
            ]["$ref"],
        )
        self.assertEqual(
            "#/$defs/mutationReportMetrics",
            definitions["report"]["allOf"][0]["then"]["properties"]["metrics"][
                "oneOf"
            ][0]["$ref"],
        )
        self.assertEqual(
            "#/$defs/fuzzReportMetrics",
            definitions["report"]["allOf"][0]["else"]["properties"]["metrics"][
                "oneOf"
            ][0]["$ref"],
        )
        self.assertTrue(definitions["report"]["properties"]["targets"]["uniqueItems"])

    def test_decision_names_python_as_the_cross_field_semantic_authority(self):
        decision = " ".join(DECISION.read_text().split())

        self.assertIn(
            "The Python validator is authoritative for cross-field semantics that JSON Schema cannot express, including unique and sorted target names, recomputed completeness, outcomes, and aggregate metrics.",
            decision,
        )

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

    def aggregate(
        self,
        fixture,
        *,
        track,
        tool,
        expected,
        input_mutator=None,
        env=None,
        fail_on_incomplete="true",
    ):
        self.assertTrue(SCRIPT.is_file(), f"missing aggregator: {SCRIPT}")
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            shutil.copytree(FIXTURES / fixture, input_dir)
            if input_mutator is not None:
                input_mutator(input_dir)
            for invalid_fixture in input_dir.rglob("target-result.invalid"):
                invalid_fixture.rename(invalid_fixture.with_name("target-result.json"))
            output_dir = Path(temp_dir) / "output"
            github_output = Path(temp_dir) / "github-output"
            summary_path = Path(temp_dir) / "step-summary"
            command = [
                sys.executable,
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
                fail_on_incomplete,
                "--github-output",
                str(github_output),
                "--github-step-summary",
                str(summary_path),
            ]
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=30, env=env
            )
            report_path = output_dir / "report.json"
            summary_file = output_dir / "summary.md"
            self.assertTrue(report_path.is_file(), result.stderr)
            self.assertTrue(summary_file.is_file(), result.stderr)
            report = json.loads(report_path.read_text())
            summary = summary_file.read_text()
            self.assertTrue(github_output.is_file(), result.stderr)
            self.assertTrue(summary_path.is_file(), result.stderr)
            github_outputs = dict(
                line.split("=", 1) for line in github_output.read_text().splitlines()
            )
            canonical_score = ""
            if (
                report["track"] == "mutation"
                and report["metrics"] is not None
                and report["metrics"]["canonical_score_pct"] is not None
            ):
                canonical_score = "{0:.2f}".format(
                    report["metrics"]["canonical_score_pct"]
                )
            self.assertEqual(
                {
                    "report_artifact_name": "quality-report-{0}-1234567890-2".format(
                        track
                    ),
                    "completeness": str(report["completeness"]["complete"]).lower(),
                    "expected_targets": str(report["completeness"]["expected"]),
                    "reported_targets": str(report["completeness"]["reported"]),
                    "outcome": report["outcome"],
                    "canonical_score_pct": canonical_score,
                },
                github_outputs,
            )
            self.assertEqual(summary, summary_path.read_text())
            return result, report, summary

    def validate_report(self, report):
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            report_path.write_text(json.dumps(report))
            return subprocess.run(
                [sys.executable, str(SCRIPT), "validate", str(report_path)],
                capture_output=True,
                text=True,
                timeout=30,
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
