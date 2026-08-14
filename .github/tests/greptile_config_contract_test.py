import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class GreptileConfigContractTest(unittest.TestCase):
    def test_reviews_are_manual_only(self):
        with (ROOT / "greptile.json").open() as config_file:
            config = json.load(config_file)

        self.assertEqual({"skipReview": "AUTOMATIC"}, config)


if __name__ == "__main__":
    unittest.main()
