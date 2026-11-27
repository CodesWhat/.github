# Agent instructions for CodesWhat/.github

This repository is the organization's meta layer: community health defaults,
the repository onboarding checklist (`REPOSITORY_ONBOARDING.md`), reusable CI
workflows, the organization profile README and its generator, and the
contract tests that pin the shapes other repositories depend on.

## What lives here

- `.github/workflows/go-ci.yml`, `node-ci.yml`, `release-gate.yml`,
  `greptile-summon.yml`, `quality-report-aggregate.yml` — reusable workflows
  consumed by other CodesWhat repositories.
- `.github/tests/` — contract tests. These are the public API of the org's
  shared configs (the `greptile.json` shape, the quality-report v1 contract,
  the reusable CI inputs). Change a contract test and its consumers together,
  never one side alone.
- `profile/` — the organization profile README plus generated SVG assets
  from `scripts/generate_profile_svg.py`. The untracked
  `profile/font_reference.svg` is user-owned; leave it alone.

## Rules that are specific to this repository

- Consumers pin the reusable workflows to frozen full commit SHAs. Treat
  every workflow change as breaking for pinned consumers: add new inputs
  with safe defaults, never repurpose an existing input, and let consumers
  adopt by rolling their pin deliberately.
- PRs target `dev/repository-standards`. `main` advances only through
  promotion PRs. Before reconciling, account for released content:
  `git log --oneline origin/dev/repository-standards..origin/main` should list
  only the previous promotion's squash commit. Any other commit is content that
  landed on `main` directly; forward-port it to dev through a normal reviewed PR
  first, because the `ours` merge keeps dev's tree and silently drops it. Then
  reconcile:
  `git merge -s ours origin/main -m "chore(sync): reconcile main before promotion"`.
  Verify promotions with tree equality
  (`git diff --quiet origin/main origin/dev/repository-standards`), never
  commit ancestry.
- After opening a promotion PR, check its code scanning result before
  approving. `main`'s ruleset blocks merges on open alerts at high or above,
  and CodeQL here only analyzes pull requests, on `refs/pull/<n>/merge`, so a
  dev-branch query always returns 0 and proves nothing. Wait for an analysis
  of the PR's current test merge commit, then count the open alerts on that
  ref. With `n` set to the promotion PR number, run this in bash. It must
  print 0:

  ```bash
  repo="repos/CodesWhat/.github"
  ref="refs/pull/$n/merge"
  if merge="$(gh api "$repo/pulls/$n" --jq .merge_commit_sha)" &&
    analyses="$(gh api --paginate \
      "$repo/code-scanning/analyses?ref=$ref&per_page=100")" &&
    jq -se --arg m "$merge" 'any(.[][]; .commit_sha == $m)' \
      <<<"$analyses" >/dev/null &&
    alerts="$(gh api --paginate \
      "$repo/code-scanning/alerts?state=open&ref=$ref&per_page=100")"; then
    jq -s '[.[][] | .rule | select(.security_severity_level == "high"
      or .security_severity_level == "critical" or .severity == "error")]
      | length' <<<"$alerts"
  else
    echo "check failed or no code scanning analysis of the merge commit yet"
  fi
  ```

  Approve only at 0. Fix each alert, or dismiss it with a reason once it's
  verified as a false positive. Never change the ruleset to get past it.
- There is no shared admission queue; pace hosted review and heavy CI with
  the maintainer directly. Add `review-ready` for a PR's first CodeRabbit
  review and request subsequent fix-batch reviews explicitly.
  Freeze pushes while review runs, read its output and verify the covered head.
  `.coderabbit.yaml` keeps default/dev targets but disables automatic review on
  ordinary pushes. Required review and protection remain effective.
- Approve ordinary dependency updates in the Renovate dashboard one at a time.
  This repo's wrapper targets `dev/repository-standards` and limits ordinary
  PRs/branches to one at a time and one new PR per hour. The shared preset is
  unchanged; urgent vulnerability updates keep their separate handling.
- Commits are plain Conventional Commits, `<type>(scope): <description>`,
  no emoji, no AI attribution trailers.
- Never weaken branch protection or rulesets to land a change.

## Validation

Run before pushing (the lefthook pre-push hook runs the same set):

```bash
bash scripts/validate.sh
```

That script mirrors the Standards Validation CI job: contract tests, Python
compile, YAML/JSON parse, markdownlint (with the separate profile config for
`profile/README.md`), and actionlint/zizmor when installed locally.
