from pathlib import Path
import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest
import unittest.mock


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/main-is-released.yml"


def isolated_env(**extra):
    """os.environ minus GIT_* variables. Under a git hook, GIT_DIR and
    GIT_INDEX_FILE point at the real repository, and a throwaway repo's git
    calls would write there instead."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(extra)
    return env


def extract_script():
    """Return the `run: |` body of the assertion step, de-indented."""
    lines = WORKFLOW.read_text().splitlines()
    start = next(
        i for i, line in enumerate(lines)
        if "- name: Assert main points at a release tag" in line
    )
    run = next(
        i for i in range(start, len(lines)) if lines[i].strip() == "run: |"
    )
    body = []
    indent = None
    for line in lines[run + 1:]:
        if line.strip():
            width = len(line) - len(line.lstrip())
            if indent is None:
                indent = width
            elif width < indent:
                break
            body.append(line[indent:])
        else:
            body.append("")
    return "\n".join(body).rstrip() + "\n"


class MainIsReleasedContractTest(unittest.TestCase):
    def test_reusable_workflow_shape_and_read_only_permissions(self):
        workflow = self.read_workflow()

        for expected in (
            "  workflow_call:\n",
            "permissions: {}",
            "    runs-on: ubuntu-26.04",
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

        self.assertIn("git tag --points-at HEAD", workflow)
        self.assertNotIn("--exact-match", workflow)
        # --abbrev=0 alone answers "what tag is nearest", which is true of a
        # drifted main too. It may only be used to report inside the failure
        # branch, never in the condition that decides pass or fail — so the
        # decisive slice is the line reading the tags, not the whole block.
        decisive = workflow.split('tags="$(', 2)[1].split("\n", 1)[0]
        self.assertIn("--points-at HEAD", decisive)
        self.assertNotIn("--abbrev=0", decisive)

    def test_every_tag_on_head_is_considered(self):
        """Several tags can point at main. Judging only one (as describe does)
        fails a correctly released main when a non-release tag wins."""
        workflow = self.read_workflow()

        self.assertIn('done <<< "$tags"', workflow)
        self.assertIn("while IFS= read -r t", workflow)

    def test_no_tags_is_reported_as_unevaluable_not_as_drift(self):
        """A repo with zero tags produces the same 'not tagged' as one that
        drifted. Those need different answers, so the measurement proves it
        could have worked before its result is trusted."""
        workflow = self.read_workflow()

        self.assertIn('if [ -z "$(git tag)" ]; then', workflow)
        self.assertLess(
            workflow.index('if [ -z "$(git tag)" ]'),
            workflow.index("git tag --points-at HEAD"),
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
        self.assertIn("none of which is a release version tag", workflow)

        # Prerelease detection keys on the hyphen AFTER the version, not any
        # hyphen anywhere — the old `case $tag in *-*)` called `my-tag` a
        # prerelease and would have accepted it under allow-prerelease.
        self.assertNotIn('case "$tag" in', workflow)
        self.assertIn("pre_re=", workflow)
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
        self.assertIn('[ "$ALLOW_PRERELEASE" != "true" ]', workflow)
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

    def test_isolated_env_drops_hook_git_variables(self):
        """Pre-push runs these tests with GIT_DIR set to the real repo."""
        with unittest.mock.patch.dict(os.environ, {"GIT_DIR": "/real/.git", "GIT_INDEX_FILE": "/real/index"}):
            env = isolated_env(EXTRA="1")
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_INDEX_FILE", env)
        self.assertEqual("1", env["EXTRA"])

    def test_main_is_released_script_behavior(self):
        """Run the extracted bash against throwaway repos so the pass/fail
        decision is exercised, not just grepped for."""
        script_text = extract_script()
        self.assertTrue(script_text.strip())
        self.assertIn("set -euo pipefail", script_text)

        def git(repo, *args):
            subprocess.run(
                ["git", *args], cwd=repo, check=True, env=isolated_env(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

        def commit(repo, name):
            (Path(repo) / name).write_text(name)
            git(repo, "add", name)
            git(repo, "commit", "-q", "-m", name)

        def no_tags(repo):
            pass

        def untagged_head(repo):
            git(repo, "tag", "v1.0.0")
            commit(repo, "second")

        def tag_head(name):
            def setup(repo):
                git(repo, "tag", name)
            return setup

        def tag_old(repo):
            git(repo, "tag", "v1.2.3")
            commit(repo, "second")

        cases = (
            ("no tags", no_tags, "true", False, "stderr", "no tags"),
            ("untagged head", untagged_head, "true", False, "stderr", "not a tagged release"),
            ("moving tag", tag_head("snapshot"), "true", False, "stderr", "none of which is a release version tag"),
            ("release tag", tag_head("v1.2.3"), "true", True, "stdout", "released at v1.2.3"),
            ("prerelease denied", tag_head("v1.2.3-rc.1"), "false", False, None, None),
            ("prerelease allowed", tag_head("v1.2.3-rc.1"), "true", True, None, None),
            ("tag on older commit", tag_old, "true", False, None, None),
        )

        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            sleep = bin_dir / "sleep"
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(sleep.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            script = Path(tmp) / "assert.sh"
            script.write_text(script_text)
            env = isolated_env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

            for label, setup, allow, ok, stream, needle in cases:
                with self.subTest(label):
                    repo = Path(tmp) / label.replace(" ", "-")
                    repo.mkdir()
                    git(repo, "init", "-q")
                    git(repo, "config", "user.name", "test")
                    git(repo, "config", "user.email", "test@example.com")
                    git(repo, "config", "commit.gpgsign", "false")
                    git(repo, "config", "tag.gpgsign", "false")
                    commit(repo, "first")
                    setup(repo)
                    result = subprocess.run(
                        ["bash", str(script)], cwd=repo, env={**env, "ALLOW_PRERELEASE": allow},
                        capture_output=True, text=True,
                    )
                    if ok:
                        self.assertEqual(result.returncode, 0, result.stderr)
                    else:
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                    if needle:
                        self.assertIn(needle, getattr(result, stream))

    def run_check(self, setup, allow_prerelease="false"):
        """Run the real step script against a throwaway repo."""
        workflow = self.read_workflow()
        step = workflow.split("- name: Assert main points at a release tag", 1)[1]
        body = step.split("        run: |\n", 1)[1]
        script = textwrap.dedent(body)
        with tempfile.TemporaryDirectory() as tmp:
            env = isolated_env(
                ALLOW_PRERELEASE=allow_prerelease,
                GIT_CONFIG_GLOBAL=os.devnull,
                GIT_CONFIG_SYSTEM=os.devnull,
            )

            def git(*args):
                subprocess.run(
                    ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                    cwd=tmp, env=env, check=True, capture_output=True,
                )

            git("init", "-q")
            git("commit", "-q", "--allow-empty", "-m", "release")
            setup(git)
            return subprocess.run(
                ["bash", "-c", script], cwd=tmp, env=env, capture_output=True, text=True
            )

    def test_behavior_release_tag_among_non_release_tags_passes(self):
        def setup(git):
            git("tag", "v1.2.3")
            git("tag", "-a", "v1", "-m", "moving")
            git("tag", "-a", "latest", "-m", "moving")

        result = self.run_check(setup)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("main is released at v1.2.3", result.stdout)

    def test_behavior_final_release_beats_prerelease_when_prereleases_disallowed(self):
        def setup(git):
            git("tag", "-a", "v1.0.0-rc.1", "-m", "rc")
            git("tag", "v1.0.0")

        result = self.run_check(setup)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("main is released at v1.0.0", result.stdout)

    def test_behavior_only_prerelease_fails_by_default(self):
        result = self.run_check(lambda git: git("tag", "v1.0.0-rc.1"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prerelease v1.0.0-rc.1", result.stderr)

    def test_behavior_only_non_release_tag_fails(self):
        result = self.run_check(lambda git: git("tag", "latest"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("none of which is a release version tag", result.stderr)

    def read_workflow(self):
        self.assertTrue(WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}")
        return WORKFLOW.read_text()


if __name__ == "__main__":
    unittest.main()
