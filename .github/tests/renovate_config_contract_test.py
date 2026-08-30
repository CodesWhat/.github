import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class RenovateConfigContractTest(unittest.TestCase):
    def read_config(self):
        with (ROOT / "renovate-config.json").open() as config_file:
            return json.load(config_file)

    def test_gomod_updates_are_tidied(self):
        """Renovate writes the new go.sum hashes and leaves the superseded
        ones in place. `go build` tolerates that, because go.sum only has to
        be sufficient — so the break lands later, in the GoReleaser gate,
        whose `go mod tidy` removes the stale lines and dirties the tree
        against a clean-tree assertion. Measured on sockguard #343, where a
        go-containerregistry 0.21.8 to 0.21.9 bump pulled a transitive
        golang.org/x/net 0.57 to 0.58 and go.sum ended up carrying all four.

        Every Go repo in the org consumes this preset and none sets its own
        `postUpdateOptions`, so removing this reintroduces the failure on
        every future transitive bump, in a job that names neither Renovate
        nor go.sum."""
        config = self.read_config()

        self.assertIn("gomodTidy", config.get("postUpdateOptions", []))

    def test_portwing_lock_maintenance_is_disabled_exactly(self):
        config = self.read_config()

        matches = [
            rule
            for rule in config.get("packageRules", [])
            if rule.get("matchRepositories") == ["CodesWhat/portwing"]
        ]

        self.assertEqual(1, len(matches))
        self.assertEqual(["lockFileMaintenance"], matches[0].get("matchUpdateTypes"))
        self.assertIs(matches[0].get("enabled"), False)


if __name__ == "__main__":
    unittest.main()
