import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = {
    "go": ROOT / ".github/workflows/go-ci.yml",
    "node": ROOT / ".github/workflows/node-ci.yml",
    "release": ROOT / ".github/workflows/release-gate.yml",
}
TARGET_SHA = "a" * 40


class ReusableCIContractTest(unittest.TestCase):
    def test_workflow_call_interfaces_are_typed_and_have_no_outputs_or_secrets(self):
        go_inputs = [
            "module-directory",
            "go-version-file",
            "go-cache-dependency-path",
            "lint-check-name",
            "test-check-name",
            "fuzzers-json",
            "run-govulncheck",
            "run-qlty",
            "run-goreleaser",
            "run-workflow-security",
            "run-commit-message",
            "run-codeql",
        ] + self.egress_inputs(
            (
                "test",
                "lint",
                "govulncheck",
                "workflow-security",
                "commit-message",
                "goreleaser",
                "codeql",
                "qlty",
                "fuzz",
            )
        )
        node_inputs = [
            "node-version",
            "lockfile-path",
            "lint-check-name",
            "test-check-name",
            "build-check-name",
            "run-lint",
            "run-test",
            "run-build",
        ] + self.egress_inputs(("lint", "test", "build"))
        release_inputs = [
            "target-sha",
            "workflow-files-json",
            "max-attempts",
            "sleep-seconds",
        ]

        expected_inputs = {
            "go": go_inputs,
            "node": node_inputs,
            "release": release_inputs,
        }
        expected_jobs = {
            "go": {
                "test",
                "lint",
                "govulncheck",
                "workflow-security",
                "commit-message",
                "goreleaser",
                "codeql",
                "qlty",
                "fuzz",
            },
            "node": {"lint", "test", "build"},
            "release": {"gate"},
        }

        for name, path in WORKFLOWS.items():
            workflow = self.read_workflow(path)
            header = workflow.split("\npermissions:", 1)[0]
            inputs = re.findall(r"^      ([a-z][a-z0-9-]+):$", header, re.MULTILINE)
            self.assertEqual(expected_inputs[name], inputs)
            self.assertRegex(workflow, r"(?m)^on:\n  workflow_call:\s+inputs:")
            self.assertNotRegex(
                workflow,
                r"(?m)^  (push|pull_request|schedule|workflow_dispatch):",
            )
            self.assertNotIn("concurrency:", workflow)
            self.assertNotIn("outputs:", header)
            self.assertNotIn("secrets:", header)
            self.assertNotIn("secrets: inherit", workflow)
            self.assertIn("permissions: {}", workflow)

            jobs = set(
                re.findall(
                    r"^  ([a-z][a-z0-9-]+):$",
                    workflow.split("\njobs:\n", 1)[1],
                    re.MULTILINE,
                )
            )
            self.assertEqual(expected_jobs[name], jobs)

        self.assert_input("go", "module-directory", "string", default=".")
        self.assert_input("go", "go-version-file", "string", default="go.mod")
        self.assert_input("go", "go-cache-dependency-path", "string", default="go.sum")
        self.assert_input("go", "lint-check-name", "string", default="Go Lint")
        self.assert_input("go", "test-check-name", "string", default="Go Test")
        self.assert_input("go", "fuzzers-json", "string", default="[]")
        for input_name in (
            "run-govulncheck",
            "run-qlty",
            "run-goreleaser",
            "run-workflow-security",
            "run-commit-message",
            "run-codeql",
        ):
            self.assert_input("go", input_name, "boolean", default="false")

        self.assert_input("node", "node-version", "string", default="24")
        self.assert_input("node", "lockfile-path", "string", default="package-lock.json")
        self.assert_input("node", "lint-check-name", "string", default="Node Lint")
        self.assert_input("node", "test-check-name", "string", default="Node Test")
        self.assert_input("node", "build-check-name", "string", default="Node Build")
        for input_name in ("run-lint", "run-test", "run-build"):
            self.assert_input("node", input_name, "boolean", default="false")

        for workflow_name, job_names in (
            ("go", ("test", "lint", "govulncheck", "workflow-security", "commit-message", "goreleaser", "codeql", "qlty", "fuzz")),
            ("node", ("lint", "test", "build")),
        ):
            for job_name in job_names:
                self.assert_input(workflow_name, f"{job_name}-egress-policy", "string", default="audit")
                self.assert_input(workflow_name, f"{job_name}-allowed-endpoints", "string", default="")

        self.assert_input("release", "target-sha", "string", required=True)
        self.assert_input("release", "workflow-files-json", "string", required=True)
        self.assert_input("release", "max-attempts", "number", default="12")
        self.assert_input("release", "sleep-seconds", "number", default="300")

    def test_central_jobs_have_exact_names_fixed_commands_and_per_job_egress(self):
        go = self.read_workflow(WORKFLOWS["go"])
        expected_go_names = {
            "test": "${{ inputs.test-check-name }}",
            "lint": "${{ inputs.lint-check-name }}",
            "govulncheck": "Govulncheck",
            "workflow-security": "Workflow Security",
            "commit-message": "Commit Message",
            "goreleaser": "GoReleaser Config",
            "codeql": "CodeQL Analysis",
            "qlty": "Qlty Check",
            "fuzz": 'Go Fuzz (${{ matrix.fuzzer.name }})',
        }
        for job, display_name in expected_go_names.items():
            self.assertEqual(display_name, self.job_name(go, job))
            self.assert_job_egress(go, job)

        expected_go_scripts = [
            "./scripts/ci/go-test.sh",
            "./scripts/ci/go-lint.sh",
            "./scripts/ci/go-govulncheck.sh",
            "./scripts/ci/commit-message.sh",
            "./scripts/ci/go-release-check.sh",
            "./scripts/ci/go-codeql-build.sh",
            "./scripts/ci/go-qlty.sh",
            "./scripts/ci/go-fuzz.sh",
        ]
        self.assertEqual(expected_go_scripts, self.fixed_scripts(go))
        self.assertIn("fuzzer: ${{ fromJSON(inputs.fuzzers-json) }}", go)
        self.assertIn("go-version-file: ${{ inputs.go-version-file }}", go)
        self.assertIn("cache-dependency-path: ${{ inputs.go-cache-dependency-path }}", go)
        self.assertIn("MODULE_DIRECTORY: ${{ inputs.module-directory }}", go)

        node = self.read_workflow(WORKFLOWS["node"])
        expected_node_names = {
            "lint": "${{ inputs.lint-check-name }}",
            "test": "${{ inputs.test-check-name }}",
            "build": "${{ inputs.build-check-name }}",
        }
        for job, display_name in expected_node_names.items():
            self.assertEqual(display_name, self.job_name(node, job))
            self.assert_job_egress(node, job)
        self.assertEqual(
            [
                "./scripts/ci/node-lint.sh",
                "./scripts/ci/node-test.sh",
                "./scripts/ci/node-build.sh",
            ],
            self.fixed_scripts(node),
        )
        self.assertIn("node-version: ${{ inputs.node-version }}", node)
        self.assertIn("cache-dependency-path: ${{ inputs.lockfile-path }}", node)

        for workflow in (go, node, self.read_workflow(WORKFLOWS["release"])):
            self.assertIn("runs-on: ubuntu-24.04", workflow)
            for action in re.findall(r"^\s+uses: ([^\s#]+)", workflow, re.MULTILINE):
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
            for run_block in re.findall(r"run: \|\n((?: {10}.*\n|\n)+)", workflow):
                self.assertNotIn("${{ inputs.", run_block)

    def test_artifact_uploads_are_central_and_fixed(self):
        go = self.read_workflow(WORKFLOWS["go"])
        node = self.read_workflow(WORKFLOWS["node"])

        self.assertEqual(
            ["artifacts/go-test/", "artifacts/go-fuzz/"],
            re.findall(r"^\s+path: (artifacts/[^\s]+)$", go, re.MULTILINE),
        )
        self.assertEqual(
            ["artifacts/node-test/", "artifacts/node-build/"],
            re.findall(r"^\s+path: (artifacts/[^\s]+)$", node, re.MULTILINE),
        )
        for workflow in (go, node):
            upload_count = workflow.count("actions/upload-artifact@")
            self.assertEqual(upload_count, workflow.count("if-no-files-found: ignore"))
            self.assertEqual(upload_count, workflow.count("retention-days: 14"))
            self.assertEqual(upload_count, workflow.count("if: always()"))

    def test_release_gate_checks_every_workflow_by_exact_sha_push_and_nonempty_branch(self):
        exact_success = {
            "head_sha": TARGET_SHA,
            "event": "push",
            "head_branch": "dev/v1.7",
            "status": "completed",
            "conclusion": "success",
        }
        wrong_runs = [
            {**exact_success, "event": "pull_request"},
            {**exact_success, "head_branch": ""},
            {**exact_success, "head_sha": "b" * 40},
        ]
        result = self.run_release_gate(
            {
                "ci-verify.yml": wrong_runs + [exact_success],
                "e2e-playwright.yml": [
                    {**exact_success, "head_branch": "main"},
                ],
            }
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"Found successful CI Verify push run for {TARGET_SHA}.", result.stdout)
        self.assertIn(f"Found successful E2E Playwright push run for {TARGET_SHA}.", result.stdout)

    def test_release_gate_fails_closed_when_any_workflow_lacks_exact_success(self):
        result = self.run_release_gate(
            {
                "ci-verify.yml": [
                    {
                        "head_sha": TARGET_SHA,
                        "event": "push",
                        "head_branch": "main",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
                "e2e-playwright.yml": [
                    {
                        "head_sha": "b" * 40,
                        "event": "push",
                        "head_branch": "main",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ],
            }
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            f"Timed out waiting for successful E2E Playwright push run for {TARGET_SHA}.",
            result.stdout,
        )

    def test_release_gate_rejects_unsafe_inputs_before_api_calls(self):
        bad_sha = self.run_release_gate({"ci-verify.yml": []}, target_sha="main")
        bad_files = self.run_release_gate(
            {"ci-verify.yml": []},
            workflow_files_json='["../../release.yml"]',
        )
        bad_attempts = self.run_release_gate(
            {"ci-verify.yml": []},
            max_attempts="0",
        )

        self.assertNotEqual(0, bad_sha.returncode)
        self.assertIn("target-sha must be a full lowercase commit SHA", bad_sha.stdout)
        self.assertNotEqual(0, bad_files.returncode)
        self.assertIn("workflow-files-json must be a nonempty JSON array", bad_files.stdout)
        self.assertNotEqual(0, bad_attempts.returncode)
        self.assertIn("max-attempts must be a positive integer", bad_attempts.stdout)

    def test_standards_validation_runs_this_contract(self):
        workflow = self.read_workflow(ROOT / ".github/workflows/standards-validation.yml")
        self.assertEqual(1, workflow.count("python3 .github/tests/reusable_ci_contract_test.py"))

    def egress_inputs(self, job_names):
        return [item for job in job_names for item in (f"{job}-egress-policy", f"{job}-allowed-endpoints")]

    def read_workflow(self, path):
        self.assertTrue(path.is_file(), f"missing reusable workflow: {path.name}")
        return path.read_text()

    def input_header(self, workflow_name):
        workflow = self.read_workflow(WORKFLOWS[workflow_name])
        return workflow.split("\npermissions:", 1)[0]

    def assert_input(self, workflow_name, input_name, input_type, default=None, required=False):
        header = self.input_header(workflow_name)
        block = header.split(f"      {input_name}:\n", 1)[1]
        next_input = re.search(r"^      [a-z][a-z0-9-]+:$", block, re.MULTILINE)
        if next_input:
            block = block[: next_input.start()]
        self.assertIn(f"        type: {input_type}\n", block)
        if default is not None:
            expected = (
                f'        default: "{default}"\n'
                if default in {"", "[]"}
                else f"        default: {default}\n"
            )
            self.assertIn(expected, block)
        if required:
            self.assertIn("        required: true\n", block)

    def job_section(self, workflow, job_name):
        jobs = workflow.split("\njobs:\n", 1)[1]
        section = jobs.split(f"  {job_name}:\n", 1)[1]
        next_job = re.search(r"^  [a-z][a-z0-9-]+:$", section, re.MULTILINE)
        return section[: next_job.start()] if next_job else section

    def job_name(self, workflow, job_name):
        section = self.job_section(workflow, job_name)
        return re.search(r"^    name: (.+)$", section, re.MULTILINE).group(1).strip('"')

    def assert_job_egress(self, workflow, job_name):
        section = self.job_section(workflow, job_name)
        self.assertIn(f"egress-policy: ${{{{ inputs.{job_name}-egress-policy }}}}", section)
        self.assertIn(f"allowed-endpoints: ${{{{ inputs.{job_name}-allowed-endpoints }}}}", section)

    def fixed_scripts(self, workflow):
        return re.findall(r"^\s+run: (\./scripts/ci/[^\s]+)$", workflow, re.MULTILINE)

    def release_gate_script(self):
        workflow = self.read_workflow(WORKFLOWS["release"])
        marker = "      - name: Verify required CI workflows\n"
        self.assertIn(marker, workflow)
        step = workflow.split(marker, 1)[1]
        block = step.split("        run: |\n", 1)[1]
        lines = []
        for line in block.splitlines():
            if line.startswith("          "):
                lines.append(line[10:])
            elif not line:
                lines.append("")
            else:
                break
        self.assertTrue(lines, "release gate shell step is empty")
        return "\n".join(lines)

    def run_release_gate(
        self,
        runs_by_workflow,
        target_sha=TARGET_SHA,
        workflow_files_json=None,
        max_attempts="1",
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fixtures = temp / "fixtures"
            fixtures.mkdir()

            metadata = {
                "ci-verify.yml": (123, "CI Verify"),
                "e2e-playwright.yml": (456, "E2E Playwright"),
            }
            for workflow_file, runs in runs_by_workflow.items():
                workflow_id = metadata[workflow_file][0]
                (fixtures / f"{workflow_id}.json").write_text(json.dumps({"workflow_runs": runs}))

            curl = fake_bin / "curl"
            curl.write_text(
                "#!/usr/bin/env bash\n"
                "for argument in \"$@\"; do url=\"$argument\"; done\n"
                "case \"$url\" in\n"
                "  */actions/workflows/ci-verify.yml) printf '%s' '{\"id\":123,\"name\":\"CI Verify\",\"path\":\".github/workflows/ci-verify.yml\"}' ;;\n"
                "  */actions/workflows/e2e-playwright.yml) printf '%s' '{\"id\":456,\"name\":\"E2E Playwright\",\"path\":\".github/workflows/e2e-playwright.yml\"}' ;;\n"
                "  */actions/workflows/123/runs?per_page=100) cat \"$RUNS_FIXTURES/123.json\" ;;\n"
                "  */actions/workflows/456/runs?per_page=100) cat \"$RUNS_FIXTURES/456.json\" ;;\n"
                "  *) printf 'unexpected URL: %s\\n' \"$url\" >&2; exit 22 ;;\n"
                "esac\n"
            )
            curl.chmod(0o755)

            if workflow_files_json is None:
                workflow_files_json = json.dumps(list(runs_by_workflow))
            env = os.environ.copy()
            env.update(
                {
                    "GH_TOKEN": "test-token",
                    "GITHUB_API_URL": "https://api.github.test",
                    "GITHUB_REPOSITORY": "CodesWhat/example",
                    "MAX_ATTEMPTS": max_attempts,
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "RUNS_FIXTURES": str(fixtures),
                    "SLEEP_SECONDS": "0",
                    "TARGET_SHA": target_sha,
                    "WORKFLOW_FILES_JSON": workflow_files_json,
                }
            )
            return subprocess.run(
                ["bash", "-c", self.release_gate_script()],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
