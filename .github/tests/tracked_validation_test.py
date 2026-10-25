from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/validate_tracked_files.py"


class TrackedValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tracked-validation-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        subprocess.run(
            ["git", "init", "--quiet", str(self.repo)], check=True,
            capture_output=True,
        )
        self.write(".gitignore", ".planning/\n.claude/\n__pycache__/\n", tracked=True)

    def write(self, name, content, tracked=False):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if tracked:
            subprocess.run(
                ["git", "add", "--", name], cwd=self.repo, check=True,
                capture_output=True,
            )

    def run_validation(self, kind):
        return subprocess.run(
            [sys.executable, str(VALIDATOR), kind], cwd=self.repo,
            capture_output=True, text=True, timeout=60,
        )

    def assert_valid(self, kind):
        result = self.run_validation(kind)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def assert_invalid(self, kind, diagnostic):
        result = self.run_validation(kind)
        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn(diagnostic, result.stdout + result.stderr)

    def test_ignored_and_untracked_files_are_not_validation_inputs(self):
        for directory in (".planning", ".claude/worktrees/other", "scratch"):
            for suffix, content in (
                ("py", "def broken(:\n"), ("json", "{broken"),
                ("yml", "broken: ["), ("yaml", "broken: ["),
                ("md", "no heading\n"),
            ):
                self.write(f"{directory}/bad.{suffix}", content)
        self.write("scripts/space [x]\nname.py", "answer = 42\n", tracked=True)
        self.write(".github/data [x].json", '{"ok": true}\n', tracked=True)
        self.write(".github/data.yml", "ok: true\n", tracked=True)
        self.write("docs/a [x].md", "# Valid\n", tracked=True)
        for kind in ("python", "data", "markdown"):
            with self.subTest(kind=kind):
                self.assert_valid(kind)

    def test_invalid_tracked_python_fails(self):
        self.write(".github/bad [x].py", "def broken(:\n", tracked=True)
        self.assert_invalid("python", "SyntaxError")

    def test_invalid_tracked_json_fails(self):
        self.write(".github/bad [x].json", "{broken", tracked=True)
        self.assert_invalid("data", "JSONDecodeError")

    def test_invalid_tracked_yaml_fails(self):
        for suffix in ("yml", "yaml"):
            with self.subTest(suffix=suffix):
                name = f".github/bad.{suffix}"
                self.write(name, "broken: [", tracked=True)
                self.assert_invalid("data", "Psych::SyntaxError")
                self.write(name, "ok: true\n", tracked=True)

    def test_invalid_tracked_markdown_paths_are_literal(self):
        for name in ("docs/bad [x].md", ".github/hidden.md", "#note.md"):
            with self.subTest(name=name):
                self.write(name, "no heading\n", tracked=True)
                self.assert_invalid("markdown", "MD041")
                self.write(name, "# Valid\n", tracked=True)

    def test_profile_keeps_its_separate_rules(self):
        self.write("profile/README.md", "<div>Profile</div>\n", tracked=True)
        config = ".github/markdownlint-profile.yaml"
        self.write(config, (ROOT / config).read_text(), tracked=True)
        self.assert_valid("markdown")
        self.write("profile/README.md", "<div>Profile</div>   \n", tracked=True)
        self.assert_invalid("markdown", "MD009")

    def test_empty_tracked_input_sets_succeed(self):
        for kind in ("python", "data", "markdown"):
            with self.subTest(kind=kind):
                self.assert_valid(kind)


if __name__ == "__main__":
    unittest.main()
