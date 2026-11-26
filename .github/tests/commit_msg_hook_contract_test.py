from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "check-commit-msg.sh"

ACCEPTED = [
    "feat(scope): x",
    "fix!: x",
    "revert: x",
    "chore(deps/a.b-c): x",
    "Merge branch 'x'",
    "Merge pull request #1 from a/b",
    "Merge remote-tracking branch 'o/x'",
    "fixup! feat: x",
    "squash! feat: x",
    'Revert "feat: x"',
]

REJECTED = [
    "feat: ",
    "Feat: x",
    "wip: x",
    "feat(Bad Scope): x",
    "feat x",
    "feat:x",
]


def run_hook(message):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "COMMIT_EDITMSG"
        path.write_text(message, encoding="utf-8")
        return subprocess.run(
            ["bash", str(HOOK), str(path)], capture_output=True, text=True
        )


class CommitMsgHookContractTest(unittest.TestCase):
    def test_accepts_valid_subjects(self):
        for subject in ACCEPTED:
            with self.subTest(subject=subject):
                result = run_hook(subject + "\n")
                self.assertEqual(0, result.returncode, result.stderr)

    def test_rejects_invalid_subjects(self):
        for subject in REJECTED:
            with self.subTest(subject=subject):
                result = run_hook(subject + "\n")
                self.assertEqual(1, result.returncode)
                self.assertIn(subject, result.stderr)
                self.assertIn("plain Conventional Commits", result.stderr)

    def test_only_first_line_is_checked(self):
        result = run_hook("feat: x\n\nthis body is not a conventional subject\nwip: junk\n")
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
