#!/usr/bin/env bash
# Local mirror of the Standards Validation CI job. Run before pushing;
# lefthook pre-push runs this same script.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "==> clean tree"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "commit or stash local changes before pushing" >&2
  exit 1
fi

echo "==> contract tests"
python3 .github/tests/community_health_contract_test.py
python3 .github/tests/greptile_summon_contract_test.py
python3 .github/tests/greptile_config_contract_test.py
python3 .github/tests/quality_report_contract_test.py
python3 .github/tests/reusable_ci_contract_test.py
python3 .github/tests/renovate_config_contract_test.py

echo "==> compile python"
python3 -m compileall -q .

echo "==> parse yaml and json"
ruby -e 'require "yaml"; Dir.glob("**/*.{yml,yaml}", File::FNM_DOTMATCH).sort.each { |path| YAML.parse_file(path) }'
python3 -c 'import json; from pathlib import Path; [json.load(path.open()) for path in Path(".").rglob("*.json")]'

echo "==> markdownlint"
npx --yes markdownlint-cli2@0.23.2 "**/*.md" "#profile/README.md"
npx --yes markdownlint-cli2@0.23.2 "profile/README.md" --config .github/markdownlint-profile.yaml

echo "==> actionlint"
if command -v actionlint >/dev/null 2>&1; then
  actionlint -color
else
  echo "actionlint not installed locally; CI will run it"
fi

echo "==> zizmor"
if command -v zizmor >/dev/null 2>&1; then
  zizmor --no-online-audits .github/workflows/
else
  echo "zizmor not installed locally; CI will run it"
fi

echo "==> ok"
