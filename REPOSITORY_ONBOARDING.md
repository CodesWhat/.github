# Repository onboarding

Use this checklist when a repository joins CodesWhat. Complete the mandatory
section for every repository, then add only the language and delivery checks
that apply. A repository is not compliant because it copied another project's
files. Its hooks, workflows, and required checks must exercise its own build and
release paths.

This document defines the target state for a repository being onboarded. Its
presence in the standards repository does not certify that an existing
repository has already passed the checklist.

Each item is either a requirement, which applies when its trigger does and
protects a named outcome, or a recommended recipe. A repository that already
meets the outcome another way keeps its choice: effective existing alternatives
are accepted without a waiver. Established products keep their current release
and verification contracts until a scoped change updates them.

## 1. Classify the repository

- [ ] Record its purpose, owner, visibility, and lifecycle: public product,
  public infrastructure, permanently private, or pre-public.
- [ ] Set `main` as the default branch and record the repository's branch and
  release model in `AGENTS.md`. Recommended recipe for a tagged product: one
  active development or integration branch that ordinary changes merge into,
  with `main` advancing only through a reviewed promotion PR. Trunk development
  with immutable release tags, or a site that verifies its deployed commit, is
  equally acceptable when it fits the artifact.
- [ ] Decide whether it publishes any artifact: a binary, package, container,
  website, documentation site, Homebrew formula or cask, or GitHub release.
- [ ] Inventory its languages, package managers, generated files, external
  services, and integration-test dependencies. This inventory determines the
  conditional CI in section 4.

The mandatory controls below apply even to documentation and infrastructure
repositories. Language-specific tests do not.

## 2. Add the mandatory repository files

### Ownership and contributor instructions

- [ ] Add `.github/CODEOWNERS` with this catch-all rule:

  ```text
  * @scttbnsn @ALARGECOMPANY @biggest-littlest
  ```

  Keep the catch-all even if narrower path rules are added later. All three
  accounts must remain owners of paths not matched by a narrower rule.

- [ ] Add `.planning/` to the root `.gitignore` before anything is written
  there. It is the only home for internal planning: roadmaps with internal
  sequencing, competitor analysis, evals, mockups, handoff notes, archived
  scratch. Nothing tracked may read from it, since a fresh clone has none of
  it. Verify on the default branch, not with `git check-ignore`, which also
  passes off a local exclude. Shape: `standards/planning.md` in `ops`.
- [ ] Add a root `AGENTS.md`. Make it specific enough for a new contributor or
  coding agent to work without reverse-engineering the repository. Include the
  repository purpose and layout, exact build/test/lint commands, test
  conventions, commit convention, hook behavior, release or branch model, and
  any invariants that must not be broken. Do not put secrets or local-only
  planning details in it.
- [ ] Add `CONTRIBUTING.md` when the repository accepts contributions. It must
  agree with `AGENTS.md` about commands, branches, reviews, tests, and commits.
- [ ] Rely on this repository's org-default community-health files
  (CONTRIBUTING baseline, `SECURITY.md`, issue forms, PR template,
  `CODE_OF_CONDUCT.md`). Add a local override only when the repository needs
  repo-specific content, never as a reflex copy.
- [ ] Public product repositories: enable GitHub Discussions and state the
  routing in the README's Community section (durable bugs and feature
  requests go to Issues, open-ended Q&A and ideas to Discussions, chat to
  Discord). Meta and infrastructure repositories keep Discussions off.
- [ ] Add tool-specific contributor instructions only when the tool is used.
  For example, `CLAUDE.md` can supplement `AGENTS.md`, but it does not replace
  the required `AGENTS.md`.

### Security, license, and repository metadata

- [ ] Add a root `SECURITY.md` that names supported versions, defines the
  security scope, tells reporters not to open a public issue, lists
  `security@codeswhat.com`, and gives response expectations. Link to the
  repository's private vulnerability reporting page when GitHub supports it;
  otherwise document email or another private reporting channel.
- [ ] Enable private vulnerability reporting when GitHub offers it for the
  repository. Enable secret scanning and push protection when the visibility
  and current GitHub plan support them.
- [ ] Add the intended license in a root `LICENSE` file and verify that GitHub
  detects the expected SPDX license. A private or pre-public repository still
  needs an explicit distribution decision; do not copy a product's license by
  default.
- [ ] Set a concise GitHub description and the useful, specific topics someone
  would search for. Include the primary language and product category. Add the
  canonical homepage or documentation URL when one exists.
- [ ] Add or update a root `README.md`, remove placeholder text, and state what
  the repository is, its current maturity, and how to run or consume it.

### Dependency updates and automated review

- [ ] Give the repository an automated dependency-update path that includes
  the lockfile changes. Recommended recipe: `renovate.json` with the
  organization preset:

  ```json
  {
    "$schema": "https://docs.renovatebot.com/renovate-schema.json",
    "extends": ["local>CodesWhat/.github:renovate-config"]
  }
  ```

  A working Dependabot or equivalent setup is an accepted alternative.

- [ ] With Renovate on a repository that uses an integration branch, add exactly
  one `baseBranchPatterns` entry for the active branch. Rotate it when the next
  integration branch is cut. Do not use a pattern that matches old and current
  branches because Renovate will open a duplicate PR set against every match.
- [ ] Add only repository-specific Renovate overrides that have a written
  reason. Keep lockfile updates enabled and verify dependency PRs contain the
  lockfile changes required by a clean install.
- [ ] When using Renovate, confirm its GitHub app can access the repository. App
  access is managed at the organization level; do not widen a public-only
  installation to private repositories as an onboarding shortcut.
- [ ] Where CodeRabbit is adopted, use a root `.coderabbit.yaml` appropriate to
  the repository. Adopt intentional admission rather than automatically spending
  review capacity on every in-progress push. Scope opt-in and incremental-review
  changes through normal review, preserving required review evidence. Verify
  non-default integration targets and stacked PR behavior before relying on them.
- [ ] Confirm CodeRabbit can access the repository and posts an actual review
  comment. A green check without review output is not enough for this test.
  Inspect the effective plan, selected-file limit and remaining capacity; public
  repository visibility is not a promise of unlimited review. A quota-only green
  check is not delivered review. Required non-author approval still applies, and
  multiple accounts operated by one person are not independent human review.
- [ ] Keep `greptile.json` at exactly `{"skipReview": "AUTOMATIC"}`
  (contract-tested in this repository) so Greptile never reviews unbidden. A
  repository that wants opt-in second opinions adds a label-gated caller
  workflow (`.github/workflows/greptile.yml`, firing on the `second-opinion`
  label) that calls this repository's `greptile-summon.yml` at a pinned full
  commit SHA. Pair the caller with a CodeRabbit `labeling_instructions` entry
  for `second-opinion` without automatic label application. Applying it is an
  explicit, consequence-based admission decision with available capacity.
  The label is Greptile's only automatic caller trigger; do not wire it to
  review every PR directly. Inspect the provider's resolved configuration and
  delivered review rather than assuming a legacy file or summon proves coverage.

### Capacity and pacing

There is no shared admission queue. Pace hosted review, heavy CI and
deployments so one project does not starve the shared pools, and coordinate
timing with the maintainer directly. Keep queued development in the project's
private internal roadmap.

Keep one active change per repository and at most one small dependent successor
local. Keep diffs small, aiming around 25 changed files and within the actual
reviewer limit; batching many unrelated items into one oversized PR is not
pacing.

Freeze pushes during review, then submit one batch of fixes per pass. Finish
open work before opening another PR. Keep the three-pass cap, all required
checks and valid approvals. When a provider is in cooldown, wait for recovery
without repeated review summons, workflow reruns or indefinite polling.

Ordinary dependency arrivals run at one concurrent PR and one new PR per hour
per repository, through staggered schedules or dashboard approval, with urgent
vulnerability handling kept separate. Provider settings, bot controls and
required CI changes need scoped reviewed rollouts; documentation alone does not
configure them. Metadata-only projects record applicable controls without
installing irrelevant tools or paid services. The Qlty reporting policy is in
section 4.

This repository runs those controls in its own configuration without changing
the Renovate preset that consumers extend. Its wrapper targets the integration
branch and allows one ordinary PR and branch at a time and one new PR per hour,
and dashboard updates are approved one at a time. Apply `review-ready` for a
PR's first CodeRabbit review. Later fix batches use an explicit review request,
because pushes do not start another review.

### Local gates

Run the checks CI enforces locally before pushing. Recommended recipe: a root
`lefthook.yml` with the hooks below, with install steps in `AGENTS.md`. An
existing hook runner such as Husky or pre-commit, or a documented validation
script, is accepted when it covers the same checks.

- [ ] Enforce the repository's commit convention at `commit-msg`. Without an
  existing convention, use plain Conventional Commits:
  `<type>(scope): <description>`. Allowed types are `feat`, `fix`, `docs`,
  `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, and `revert`.
  Do not weaken or bypass an existing commit-message hook.
- [ ] Make the pre-push check sequential and fail-fast. Start with a clean-tree
  check, then run the same formatter/linter, tests, build, and workflow-security
  checks that CI enforces. A missing optional local tool may be reported and
  left to CI, but a tool that is installed must not have its failure swallowed.
- [ ] Add language formatters to `pre-commit` only when they can safely operate
  on staged files. Verify the hook does not rewrite unrelated work.

Qlty is optional. Its recommended use across local and CI gates, including why
Qlty Cloud checks stay non-required, is in the Qlty subsection of section 4.

## 3. Protect `main`

The recommended model for a tagged product makes `main` the released
version, not the newest work. It always equals what users currently have, the
GA tag, and is never the GA tag plus merged work that has not shipped. In that
recipe the next version lives on its own branch, `dev/vX.Y`, which is the
integration target for feature pull requests, and prereleases are tagged there
rather than on `main`. A continuously deployed site verifies its deployed commit
instead, and a trunk model identifies releases through immutable tags and
artifacts. The branch ruleset below applies to every model.

The reason is measurement accuracy rather than tidiness. OpenSSF Scorecard,
CodeQL default-branch analysis, Dependabot alerts, README badges, and every
published vulnerability report describe the default branch and nothing else.
Unshipped work on `main` makes all of them describe software no user runs.

For a released-`main` repository, one invariant covers it, and it is
mechanical rather than a judgement call:

```sh
git describe --exact-match origin/main
```

Every commit on `main` is a tagged release, so an untagged `main` head is
itself the alarm. Check the tag rather than auditing what a promotion diff
contains. Call this repository's `main-is-released.yml` on a schedule to
enforce it; see section 4.

`main` advances only through a promotion pull request from `dev/*` or
`maintenance/*` that is tagged on merge. There is no documentation, README, or
generated-asset exception: a second path into `main` is a second thing that
drifts, and an incorrect README is a defect in the released version like any
other. Fix it on a hotfix branch and cut a patch release. For that to stay
honest, a documentation-only release must skip artifact publication —
GoReleaser, signing, deb/rpm, Homebrew, npm — or maintainers will route around
the rule. Generated assets committed to the repository, such as a chart
SVG, regenerate at the release cut rather than on a schedule that commits to a
branch, so they cannot change underneath a tag.

Adopting this on a repository whose `main` is already ahead of its newest tag
means cutting a release at the next appropriate boundary. Never move published
history backward to adopt the model.

Create an active branch ruleset named `Main branch protection`, targeting only
the default branch. Its baseline is:

- [ ] Prevent branch deletion and non-fast-forward pushes. Add no bypass actor.
- [ ] Require pull requests with **2 approvals**.
- [ ] Require code-owner review, dismiss stale approvals after new commits, and
  require approval of the most recent push by someone other than its author.
- [ ] Require the branch to be up to date before merge.
- [ ] Require every stable, blocking PR job from the primary CI workflow. Use
  the exact job display names GitHub reports, including punctuation and emoji.
  Never type a context from memory.
- [ ] Require CodeQL results when CodeQL supports the repository's language and
  the CodeQL job has completed successfully on a representative PR.

Build the workflows and run a representative PR before adding required status
contexts. A required context must be emitted on every PR to `main`, including a
documentation-only PR. Scheduled, release-only, advisory, matrix-generated,
path-conditional, and secret-dependent jobs are not safe required contexts
unless the workflow also emits one stable aggregate result on every PR.

Rulesets are not enforced for private organization repositories on the current
GitHub Free plan. Record that limitation instead of claiming a private repo is
protected or weakening the baseline. A repository that needs production-grade
protection must be public or wait for a plan that enforces the same rules on
private repositories.

## 4. Add only the CI the repository needs

The primary CI workflow must run on PRs and pushes to every protected or active
integration branch. Add `merge_group` when the repository uses GitHub's merge
queue. Pin actions to full commit SHAs, give each job minimum permissions, set
timeouts and concurrency, disable persisted checkout credentials unless a job
must push, and use `step-security/harden-runner` with the smallest practical
egress allowlist.

Every repository with executable code needs the applicable format or lint and
test gates. Require coverage when the language and test tooling measure it
(uploaded to Codecov, not Qlty Cloud), a
production build when the repository ships a buildable artifact, dependency
review when it has dependencies, workflow validation when it has GitHub Actions,
and CodeQL when its language is supported. Add the matching language or artifact
checks below:

- **Go:** `gofmt`, `go vet`, `golangci-lint`, `go test -race`, coverage,
  `govulncheck`, and a build using the version in `go.mod`.
- **JavaScript or TypeScript:** clean lockfile install, Biome or the chosen
  linter, typecheck, unit tests with coverage, workspace build, and a lockfile
  consistency check.
- **Python:** locked or reproducible dependency install, formatter/linter,
  typecheck when used, tests with coverage, and package build when packaged.
- **Shell:** `shellcheck` plus behavioral tests for scripts that perform
  release, migration, or destructive operations.
- **GitHub Actions:** actionlint and zizmor on every PR, plus tests for
  non-trivial embedded shell and workflow contracts.
- **Container image:** build the real Dockerfile, smoke-test the image, and scan
  the built image and locked dependencies with Grype. Trivy is deprecated
  organization-wide in favor of Grype; do not adopt it, including as a Qlty
  plugin.
- **Public repository:** OpenSSF Scorecard on its supported triggers and
  dependency review on PRs.

Add these only when the behavior exists:

- [ ] Native fuzz smoke in primary CI and longer scheduled fuzzing for parsers,
  protocol boundaries, untrusted input, or an existing fuzz corpus. Do not add a
  Go fuzz workflow to a repository with no Go fuzz target.
- [ ] Browser end-to-end tests for a shipped web UI.
- [ ] Real-service or real-engine integration tests when mocks cannot exercise
  an external contract such as Docker, Podman, a registry, or a database.
- [ ] Benchmarks, mutation tests, load tests, and soak tests for a measured risk
  with a clear enforced or advisory threshold.
- [ ] Translation synchronization only for a repository with a translation
  source of truth and configured provider credentials.
- [ ] Release-invariant enforcement for a repository that defines `main` as its
  tagged release. Call this repository's `main-is-released.yml` at a pinned
  full commit SHA from a thin caller on `schedule` plus `push` to `main` plus
  `workflow_dispatch`. A reusable workflow cannot raise the permissions its
  caller grants, so a caller with top-level `permissions: {}` must grant
  `contents: read` on the calling job:

  ```yaml
  on:
    schedule: [{cron: "23 7 * * *"}]
    push:
      branches: [main]
    workflow_dispatch:
  permissions: {}
  jobs:
    released:
      permissions:
        contents: read
      uses: CodesWhat/.github/.github/workflows/main-is-released.yml@<SHA>
  ```

  It asserts the section 3 invariant: an
  untagged `main` head fails, and so does a prerelease tag unless the caller
  passes `allow-prerelease: true`. A repository that never tags, such as a
  continuously deployed site or a meta repository, should not call this at all
  rather than call it with a carve-out; for those, "`main` equals production"
  is enforced by the deploy.
- [ ] A star chart is optional. A public repository that shows one uses Star
  History: open it for the actual owner and repository, copy its current README
  snippet, and verify the rendered README in both themes while signed out.
  Generate a fresh snippet for that repository and never paste a raw GitHub
  credential into the README. A hosted chart is live presentation rather than
  a release artifact, so it needs no refresh workflow.
- [ ] A repository that still commits a first-party star-history SVG pair keeps
  its pinned `starchart-refresh.yml` caller, on `release: [published]` plus
  `workflow_dispatch` with the job granted `contents: write`, until it moves to
  Star History through its own reviewed change. Trace other consumers of the
  committed SVGs, such as a website, before removing them or the caller. The
  reusable workflow keeps its interface for existing pins.

### Qlty

Public projects run routine quality checks in their own CI and useful local
hooks. Keep Qlty CLI where it aggregates useful checks; effective native tools
are also valid. Qlty Cloud supplies periodic reporting for existing dashboards
and README metrics. A Cloud subscription is not needed for standalone CLI checks.

- [ ] Record adoption work or a concrete applicability decision in the project's
  private internal roadmap, with an owner, next action and checkable completion
  conditions. Metadata-only repositories do not need a new scanner or dashboard
  without a useful signal.
- [ ] Where Qlty CLI is used, commit `.qlty/qlty.toml` with exclusions for
  generated, minified and vendored paths. Review changes to shared CLI/Cloud
  configuration as quality-gate changes. Preserve useful existing checks rather
  than duplicating them through another tool.
- [ ] Run the repository's quality gate in CI and useful local hooks. Advisory
  duplication/complexity reports must not mask a gate failure. The shared
  `go-ci.yml` supports `run-qlty`, which invokes `scripts/ci/go-qlty.sh` in the
  caller repository. Keep the existing input default and check name; consumers
  adopt changes through deliberate full-SHA pin updates. Node repositories can
  use the SHA-pinned `qltysh/qlty-action/install` and their own gate script.
- [ ] For existing Qlty Cloud maintainability reporting, set default-branch
  Analysis Frequency to `1 month` in Project Settings > General. A manual scan
  for a release or important change is acceptable within the usage budget. The
  README maintainability grade represents the last completed scan and may be up
  to a month behind the current default branch.
- [ ] Avoid redundant hosted PR analysis after equivalent repository-owned CI
  gates are verified. Verify the control stops analysis compute; turning off
  comments or commit statuses alone does not establish that scans stop. Do not
  weaken branch protection. Any replacement for a required gate goes through a
  reviewed repository change and must be verified on the intended commit first.
- [ ] Preserve useful coverage reporting, enforcement and badges, including
  Codecov where already adopted. Each Qlty coverage upload costs one standard
  analysis minute, including each separately uploaded server-merge part. Count
  all workspace scans, uploads, runner multipliers and retries before claiming
  the allowance is sufficient. Do not silently reduce PR coverage enforcement
  to save reporting minutes.
- [ ] Verify provider-side commit identity, actual analysis/coverage content and
  badge freshness after changing settings. Classify quota failures separately
  from real analysis/upload failures; a successful upload step or status alone
  does not establish delivery or a completed scan.

This operating policy retains useful Qlty CLI and periodic Cloud reporting. It
does not introduce Sonar, require a paid upgrade, or automatically enroll every
repository in a hosted service. The reusable workflows keep their existing
interfaces and opt-in defaults; a caller's workflow does not configure Qlty
Cloud's service-side scheduling.

See Qlty's [analysis frequency](https://docs.qlty.sh/cloud/analysis-frequency),
[analysis-minute accounting](https://docs.qlty.sh/cloud/billing/analysis-minutes),
and [CLI versus Cloud](https://docs.qlty.sh/cli-vs-cloud) documentation.

## 5. Add delivery controls only for shipped artifacts

- [ ] Keep release construction separate from ordinary CI. A release-cut
  workflow should validate the version and changelog, prove the releasable tree
  is the intended tree, and create the tag from `main` only after CI succeeds.
- [ ] When a release syncs an integration branch to `main`, compare the two
  trees with `git diff --quiet`, not commit ancestry. CodesWhat repositories
  may squash-merge, so identical trees do not necessarily share the expected
  ancestor. Derive the expected active branch from release state instead of
  pinning a second hand-maintained value inside the gate.
- [ ] Publish from the tag, not from an unreviewed working tree. Verify every
  uploaded artifact after publication.
- [ ] For binaries, build the supported OS/architecture matrix and publish
  checksums and SBOMs.
- [ ] For container images, publish immutable digests, sign with Cosign, attach
  provenance and an SBOM, scan the final image, and verify the signature and
  attestation from the registry.
- [ ] For packages, formulas, casks, sites, or documentation, test the consumer
  installation or deployed URL rather than treating upload success as release
  success.
- [ ] Give release jobs only the permissions they need. Use GitHub environments
  or trusted publishing for credentials when supported.

## 6. Prove onboarding is complete

- [ ] Parse every JSON and YAML configuration. Record and run the repository's
  exact validation commands in `AGENTS.md`.
- [ ] Run `npx --yes markdownlint-cli2@0.23.2 '**/*.md'` when Markdown is
  present and `python3 -m compileall -q .` when Python is present, or the
  stricter version-pinned commands already declared by the repository.
- [ ] Run `find .github/workflows -maxdepth 1 -type f \( -name '*.yml' -o -name
  '*.yaml' \) -exec actionlint {} +` and `zizmor .github/workflows/` when GitHub
  Actions workflows are present.
- [ ] Install dependencies from a clean checkout, run the full local hook
  pipeline, and run the production build.
- [ ] Open a feature PR into the repository's integration target and, when it
  promotes to `main`, a promotion PR. Use the model recorded in section 1. Wait
  for all CI and the delivered review the repository requires on each, address
  every actionable finding, and obtain the required non-author and code-owner
  approvals.
- [ ] Confirm the PR cannot merge while a required job is pending or failing.
  Never weaken branch protection to perform this test or to clear
  `REVIEW_REQUIRED`.
- [ ] Merge, fetch the PR URL, and read the merged result. For a site or other
  deployed output, fetch the public URL and inspect the rendered result too.
- [ ] Re-read the live GitHub ruleset, repository metadata, topics, license
  detection, app access, and required check list. Compare the ruleset against
  the successful PR run rather than against a hand-maintained expected value.

Onboarding is done only when the files, live GitHub settings, local gates, and a
real PR all agree. A checked box in a tracking document is not evidence by
itself.
