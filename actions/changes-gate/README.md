# changes-gate

A composite action that answers one question for a pull request: did any
file that matters change? Use it to skip expensive jobs (macOS builds,
browsers, fuzzing) when a PR only touches docs.

## Usage

```yaml
jobs:
  changes:
    runs-on: ubuntu-26.04
    permissions:
      contents: read
      pull-requests: read
    outputs:
      run: ${{ steps.gate.outputs.run }}
    steps:
      - id: gate
        uses: CodesWhat/.github/actions/changes-gate@<full sha>
        with:
          patterns: |
            Sources/**
            Package.swift
            !**/*.md

  macos-build:
    needs: changes
    if: needs.changes.outputs.run == 'true'
    runs-on: macos-26
    steps:
      - run: echo "expensive work"
```

Pin the action to a full commit SHA. No checkout is needed.

## Contract

- `patterns` (required): newline-separated globs. `**` matches across
  directories, `*` and `?` stay inside one path segment. `#` comments and
  blank lines are ignored. A leading `!` excludes. Matching is
  case-insensitive.
- `github-token`: defaults to `github.token`.
- Output `run` is exactly `true` or `false`.

`pull_request` and `pull_request_target` evaluate the PR's changed files
(`filename` and, for renames, `previous_filename`). Every other event
returns `true`. The result is also `true` when the pattern list has no
includes, the API errors or times out, the response can't be parsed, or the
PR has 3000 or more files. `false` only means the listing completed and
nothing matched.

The script is Python 3 standard library only, reads everything from
environment variables, and logs only counts and the decision, never file
names or patterns.

## Why a job-level gate

A workflow-level `paths:` filter means the workflow never starts, so a
required status check from it stays pending forever and blocks the merge. A
job skipped by `if:` reports success to a required status check, so the PR
can merge.

A `code_scanning` ruleset rule is different. A skipped CodeQL job doesn't
produce the SARIF upload that rule waits for, so don't gate CodeQL this way.
