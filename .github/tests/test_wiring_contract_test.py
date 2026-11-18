from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / ".github" / "tests"
VALIDATE = ROOT / "scripts" / "validate.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "standards-validation.yml"


class TestWiringContractTest(unittest.TestCase):
    def test_every_contract_test_runs_once_in_validate_and_ci(self):
        tests = sorted(path.name for path in TESTS.glob("*_test.py"))
        self.assertIn("test_wiring_contract_test.py", tests)
        for label, path in (("validate.sh", VALIDATE), ("standards-validation.yml", WORKFLOW)):
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
            for name in tests:
                command = "python3 .github/tests/{0}".format(name)
                with self.subTest(file=label, test=name):
                    self.assertEqual(1, lines.count(command), command)


if __name__ == "__main__":
    unittest.main()
