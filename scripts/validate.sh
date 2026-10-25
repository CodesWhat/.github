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
python3 .github/tests/starchart_refresh_contract_test.py
python3 .github/tests/main_is_released_contract_test.py
python3 .github/tests/tracked_validation_test.py

echo "==> compile python"
python3 scripts/validate_tracked_files.py python

echo "==> parse yaml and json"
python3 scripts/validate_tracked_files.py data

echo "==> markdownlint"
python3 scripts/validate_tracked_files.py markdown

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
