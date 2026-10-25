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
  promotion PRs. Reconcile before promoting:
  `git merge -s ours origin/main -m "chore(sync): reconcile main before promotion"`.
  Verify promotions with tree equality
  (`git diff --quiet origin/main origin/dev/repository-standards`), never
  commit ancestry.
- Admit new hosted review/heavy CI work through the shared capacity roster.
  Keep preparation local while queued. After admission, add `review-ready` for
  the first CodeRabbit review; request subsequent fix-batch reviews explicitly.
  Freeze pushes while review runs, read its output and verify the covered head.
  `.coderabbit.yaml` keeps default/dev targets but disables automatic review on
  ordinary pushes. Required review and protection remain effective.
- Approve ordinary dependency updates in the Renovate dashboard only after
  admission. This repo's wrapper limits ordinary PRs/branches to one at a time
  and one new PR per hour. The shared preset is unchanged; urgent vulnerability
  updates keep their separate handling.
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
