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
    if: >-
      ${{ !cancelled() &&
      (needs.changes.result != 'success' ||
      needs.changes.outputs.run != 'false') }}
    runs-on: macos-26
    steps:
      - run: echo "expensive work"
```

Pin the action to a full commit SHA. No checkout is needed.

Don't write the caller's `if:` as `needs.changes.outputs.run == 'true'`. A
bare condition carries an implicit `success()`, so if the gate job fails or
times out the gated job is skipped and a required check passes. The
expression above runs the job unless the gate succeeded and said `false`, so a
gate failure means run.

On a fork `pull_request` the gate isn't a security boundary, because the PR
author controls the workflow file and can change or drop the gate. It matters
as a boundary under `pull_request_target`, and elsewhere it only makes honest
skip decisions.

## Contract

- `patterns` (required): newline-separated globs. `**` matches across
  directories and must be a whole path segment (`a/**/b`, not `a**b`).
  `*`, `?` and `[abc]`, `[a-z]`, `[!x]` classes stay inside one path
  segment.
  A leading `./` is ignored and a trailing `/` means `/**`. An unparseable
  glob makes the result `true`: an unclosed `[`, a bad range, `**` inside a
  segment, braces (`{` or `}`), a leading `/`, or a POSIX class (`[[:`).
  `#` comments and blank lines are ignored. A leading `!` excludes. Include
  patterns match case-insensitively, since over-triggering is safe. Exclude
  patterns are case-sensitive, so `!docs/**` never hides `Docs/x`.
- `github-token`: defaults to `github.token`.
- Output `run` is exactly `true` or `false`.

`pull_request` and `pull_request_target` evaluate the PR's changed files
(`filename` and, for renames, `previous_filename`). Every other event
returns `true`. The result is also `true` when the pattern list has no
includes, the API errors or times out, the response can't be parsed, or the
PR has 3000 or more files. It also returns `true` when the files listed
don't add up to the PR's `changed_files` count, or when pagination (the
`Link` header) is malformed or inconsistent. `false` only means the listing
completed and nothing matched.

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
