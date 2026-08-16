#!/usr/bin/env bash
# commit-msg hook: enforce plain Conventional Commits on the subject line.
set -euo pipefail

msg_file="$1"
subject="$(head -n 1 "$msg_file")"

case "$subject" in
  "Merge branch '"*|"Merge pull request #"*|"Merge remote-tracking branch '"*|fixup!\ *|squash!\ *) exit 0 ;;
esac

if [[ "$subject" =~ ^Revert\ \".+\"$ ]]; then
  exit 0
fi

pattern='^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([a-z0-9./-]+\))?!?: [^[:space:]]'
if ! [[ "$subject" =~ $pattern ]]; then
  {
    echo "commit subject must be plain Conventional Commits:"
    echo "  <type>(scope): <description>"
    echo "allowed types: feat fix docs style refactor perf test build ci chore revert"
    echo "got: $subject"
  } >&2
  exit 1
fi
