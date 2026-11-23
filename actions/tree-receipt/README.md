# tree-receipt

Run expensive verification once per tree. In a dev-branch repo the same tree
can run full CI three or four times (feature PR, push to dev, promotion PR,
push to main). These two composite actions let later events reuse the first
run's result and fall back to a full run on any doubt.

- `verify` runs first. It outputs `reuse=true` only when a trusted earlier
  push run verified exactly this tree.
- `record` runs last, in a final gate job on push events, and writes the
  receipt that a later `verify` looks for.

Required check names keep reporting. Callers skip the expensive jobs with a
job-level `if`, and a skipped job reports success to branch protection.

## Usage

```yaml
permissions: {}

jobs:
  verify:
    runs-on: ubuntu-26.04
    permissions:
      actions: read
      contents: read
      pull-requests: read
      statuses: read
    outputs:
      reuse: ${{ steps.receipt.outputs.reuse }}
    steps:
      - uses: actions/checkout@<full sha>
        with:
          fetch-depth: 2
          persist-credentials: false
      - id: receipt
        uses: CodesWhat/.github/actions/tree-receipt/verify@<full sha>

  build-and-test:
    needs: verify
    if: ${{ !cancelled() && (needs.verify.result != 'success' || needs.verify.outputs.reuse != 'true') }}
    runs-on: macos-26
    steps:
      - uses: actions/checkout@<full sha>
        with:
          persist-credentials: false
      - run: echo "expensive work"

  gate:
    needs: [verify, build-and-test]
    if: always()
    runs-on: ubuntu-26.04
    permissions:
      contents: read
      statuses: write
    steps:
      - name: Fail on any failed or cancelled job
        env:
          RESULTS: ${{ join(needs.*.result, ' ') }}
          VERIFY_RESULT: ${{ needs.verify.result }}
          REUSE: ${{ needs.verify.outputs.reuse }}
          BUILD_RESULT: ${{ needs.build-and-test.result }}
        run: |
          if [ "$BUILD_RESULT" = skipped ] \
              && { [ "$VERIFY_RESULT" != success ] || [ "$REUSE" != true ]; }; then
            echo "expensive job skipped without a verified reuse"; exit 1
          fi
          for result in $RESULTS; do
            case "$result" in
              success|skipped) ;;
              *) echo "a needed job ended as $result"; exit 1 ;;
            esac
          done
      - uses: actions/checkout@<full sha>
        if: needs.build-and-test.result == 'success'
        with:
          persist-credentials: false
      - uses: CodesWhat/.github/actions/tree-receipt/record@<full sha>
        if: needs.build-and-test.result == 'success'
```

The gate accepts `success` or `skipped`, but a skipped expensive job only
passes when `verify` succeeded with `reuse=true`. It records only when the
expensive jobs actually ran and passed, so reused evidence is never
re-recorded and its age is never refreshed.

A plain `if: needs.verify.outputs.reuse != 'true'` carries an implicit
`success()`. If `verify` fails, the expensive job would be skipped and the
gate would see only skipped jobs, so the job-level `if` above must run the
expensive job whenever `verify` did not succeed.

Pin both actions to a full commit SHA. Both need a checkout. For `push` runs
`verify` also looks at the pushed commit's parents, so check out with
`fetch-depth: 2`. On a shallow clone with no parents it logs `shallow checkout,
parents unavailable` and still uses the associated PR heads.

## Trust boundary

Commit statuses are lookup hints only. Any workflow in the repository with
`statuses: write` can post a status that names any tree and cites any run, so
a status never proves anything by itself. `verify` accepts a cited run only
when all of these hold:

- the run was a `push` run, in this repository, of this workflow file, with
  conclusion `success`, younger than `max-age-hours`, and `head_sha` equal to
  the candidate commit;
- the commit API reports that run commit's tree as exactly the tree checked
  out now, which is also the tree the receipt claims;
- the workflow file's blob at the run's commit equals its blob at the default
  branch's current head (compared by immutable commit sha, not branch name);
- every job in the run concluded `success`. Skipped, neutral, cancelled and
  similar jobs reject the run.

Pull request runs are never evidence, because their synthetic merge commit
can't be authenticated against a tree the API reports. `schedule`,
`workflow_dispatch`, `pull_request_target` and other source events are rejected
for the same reason. Any API error, malformed data, redirect or ambiguity
outputs `false`.

`verify` inputs: `max-age-hours` (default `168`) and `github-token`. Outputs:
`reuse` (exactly `true` or `false`) and `evidence-run` (the run URL, or empty).

Supported callers are `pull_request` and `push`; other events (`schedule`,
`workflow_dispatch`, `merge_group`, `workflow_call`) always output `false`.
Candidates are narrow. For `pull_request` that is the PR head SHA, compared
against the caller's checkout tree. For `push` it is the pushed commit's parents
(so a merge commit finds the dev head) plus the head SHAs of PRs associated
with the pushed commit, up to 10 commits.

A candidate's commit status is considered only when all of these hold:

- the context is `tree-receipt/<this workflow's file path>`, so receipts never
  cross workflows;
- the state is `success` and the creator is `github-actions[bot]`;
- the description is exactly `tree=<current tree>`;
- the target URL is a run URL for this repository.

At most five distinct runs are checked, and the age is the original run's age.
Reuse never refreshes it. The log carries only SHAs, counts, run ids and the
decision.

`record` writes a `success` commit status on `github.sha`, and only for `push`
events. The tree is that commit's tree (`git rev-parse "$GITHUB_SHA^{tree}"`).
Context is `tree-receipt/<workflow path>`, description is `tree=<40-hex tree>`,
and the target URL is this run. It needs `statuses: write` and a checkout that
contains the pushed commit. `pull_request`, `pull_request_target` and every
other event write no receipt and log why. The old `tested-tree` input and
`TREE_RECEIPT_TESTED_TREE` override are removed, so the tree can't be chosen by
the caller. If the write fails or is redirected it logs and exits 0, because a
missing receipt only costs a full run later.

## What must not be gated by reuse

Equal trees do not make these safe to skip. Leave them outside the reuse gate
so they run on every event:

- time-sensitive scans: vulnerability and advisory checks, whose answer changes
  without the tree changing;
- release-specific checks: signing, notarization, provenance, version and
  ancestry checks, hotfix preservation, deploys;
- anything whose inputs live outside the tree: external services, mutable tags
  and images.

Tree equality covers the workflow files, pinned reusable-workflow SHAs and
lockfiles, since they are all in the tree. It does not cover runner image
drift. `max-age-hours` bounds that.

`code_scanning` ruleset rules still need SARIF for the current commit. Don't
gate CodeQL uploads with this unless the repository's rules allow it.
