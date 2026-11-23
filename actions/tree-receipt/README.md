# tree-receipt

Run expensive verification once per tree. In a dev-branch repo the same tree
can run full CI three or four times (feature PR, push to dev, promotion PR,
push to main). These two composite actions let later events reuse the first
run's result and fall back to a full run on any doubt.

- `verify` runs first. It outputs `reuse=true` only when a trusted earlier
  run verified exactly this tree.
- `record` runs last, in a final gate job, and writes the receipt that a later
  `verify` looks for.

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
          persist-credentials: false
      - id: receipt
        uses: CodesWhat/.github/actions/tree-receipt/verify@<full sha>

  build-and-test:
    needs: verify
    if: needs.verify.outputs.reuse != 'true'
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
        run: |
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

The gate accepts `success` or `skipped` (skipped because the tree was reused)
and fails on anything else. It records only when the expensive jobs actually
ran and passed, so reused evidence is never re-recorded and its age is never
refreshed. If `verify` itself fails, `reuse` is empty, the expensive jobs run,
and the gate fails on the failed `verify` job.

Pin both actions to a full commit SHA. Both need a checkout, and for
`pull_request` runs that checkout is the synthetic merge commit. That commit's
tree is the one tested, so that is the tree recorded and compared. Pass
`tested-tree` to `record` if the tested tree is not `HEAD` in the gate job's
workspace.

## Contract

`verify` inputs: `max-age-hours` (default `168`) and `github-token`. Outputs:
`reuse` (exactly `true` or `false`) and `evidence-run` (the run URL, or empty).

`verify` looks only at narrow candidates. For `pull_request` that is the PR
head SHA. For `push` it is the pushed commit's parents (so a merge commit finds
the dev head) plus the head SHAs of PRs associated with the pushed commit, up
to 10 commits. Other events (`schedule`, `workflow_dispatch`, `merge_group`,
`workflow_call`) always output `false`.

A candidate's commit status is accepted only when all of these hold:

- the context is `tree-receipt/<this workflow's file path>`, so receipts never
  cross workflows;
- the state is `success` and the creator is `github-actions[bot]`;
- the description is exactly `tree=<current tree>`;
- the target URL is a run URL for this repository.

The run is then checked through the API. It must belong to this repository,
have the same workflow path, be completed with conclusion `success`, have
`head_sha` equal to the candidate, and be younger than `max-age-hours`. No job
in it may be failed, cancelled, timed out, action-required or startup-failed.
The age is the original run's age. Reuse never refreshes it.

Any API error, malformed data or ambiguity outputs `false`. The log carries
only SHAs, counts, run ids and the decision.

`record` writes a `success` commit status on the commit the workflow ran for
(the PR head for `pull_request`, else `github.sha`). Context is
`tree-receipt/<workflow path>`, description is `tree=<40-hex tree>`, and the
target URL is this run. It needs `statuses: write`. If the write fails it logs
and exits 0, because a missing receipt only costs a full run later. Fork PRs
never produce receipts, so they always run in full.

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
