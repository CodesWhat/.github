# Repository onboarding

Use this checklist when a repository joins CodesWhat. Complete the mandatory
section for every repository, then add only the language and delivery checks
that apply. A repository is not compliant because it copied another project's
files. Its hooks, workflows, and required checks must exercise its own build and
release paths.

This document defines the target state for a repository being onboarded. Its
presence in the standards repository does not certify that an existing
repository has already passed the checklist.

## 1. Classify the repository

- [ ] Record its purpose, owner, visibility, and lifecycle: public product,
  public infrastructure, permanently private, or pre-public.
- [ ] Set `main` as the default branch and create one active development or
  integration branch. Ordinary changes merge from a feature or fix branch into
  that branch. `main` advances only through a reviewed PR from the active
  branch, never from an independent commit or feature branch.
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

- [ ] Add a root `AGENTS.md`. Make it specific enough for a new contributor or
  coding agent to work without reverse-engineering the repository. Include the
  repository purpose and layout, exact build/test/lint commands, test
  conventions, commit convention, hook behavior, release or branch model, and
  any invariants that must not be broken. Do not put secrets or local-only
  planning details in it.
- [ ] Add `CONTRIBUTING.md` when the repository accepts contributions. It must
  agree with `AGENTS.md` about commands, branches, reviews, tests, and commits.
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

- [ ] Add `renovate.json` with the organization preset:

  ```json
  {
    "$schema": "https://docs.renovatebot.com/renovate-schema.json",
    "extends": ["local>CodesWhat/.github:renovate-config"]
  }
  ```

- [ ] If the repository uses an integration branch, add exactly one
  `baseBranchPatterns` entry for the active branch. Rotate it when the next
  integration branch is cut. Do not use a pattern that matches old and current
  branches because Renovate will open a duplicate PR set against every match.
- [ ] Add only repository-specific Renovate overrides that have a written
  reason. Keep lockfile updates enabled and verify dependency PRs contain the
  lockfile changes required by a clean install.
- [ ] Confirm the Renovate GitHub app can access the repository. App access is
  managed at the organization level; do not widen a public-only installation
  to private repositories as an onboarding shortcut.
- [ ] Add a root `.coderabbit.yaml` based on a current CodesWhat product, then
  replace its product-specific tone, ignored paths, and path instructions.
  Keep automatic review enabled. If PRs target a non-default integration
  branch, include that branch under `reviews.auto_review.base_branches` and
  request an explicit CodeRabbit review on the first PR to prove it works.
- [ ] Confirm CodeRabbit can access the repository and posts an actual review
  comment. A green check without review output is not enough for this test.
  Automated Pro-level reviews are free on public repositories only; on
  private repositories the free plan rate-limits automated reviews, and they
  have not fired for this organization in practice. Organization policy: on a
  private repository skip both CodeRabbit items, use cross-account human
  review, and add the config when the repository goes public.
- [ ] Keep `greptile.json` at exactly `{"skipReview": "AUTOMATIC"}`
  (contract-tested in this repository) so Greptile never reviews unbidden. A
  repository that wants opt-in second opinions adds a label-gated caller
  workflow (`.github/workflows/greptile.yml`, firing on the `second-opinion`
  label) that calls this repository's `greptile-summon.yml` at a pinned full
  commit SHA. Pair the caller with a CodeRabbit `labeling_instructions` entry
  for `second-opinion` and `auto_apply_labels` enabled, so applying the label
  is criteria-driven rather than left to memory. The label is Greptile's only
  trigger; never wire it to review every PR directly.

### Local gates

- [ ] Add a root `lefthook.yml` and document how contributors install Lefthook.
- [ ] Add a `commit-msg` command that enforces plain Conventional Commits:
  `<type>(scope): <description>`. Allowed types are `feat`, `fix`, `docs`,
  `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, and `revert`.
  Do not weaken or bypass an existing commit-message hook.
- [ ] Make `pre-push` sequential and fail-fast. Start with a clean-tree check,
  then run the same formatter/linter, tests, build, and workflow-security checks
  that CI enforces. A missing optional local tool may be reported and left to
  CI, but a tool that is installed must not have its failure swallowed.
- [ ] Add language formatters to `pre-commit` only when they can safely operate
  on staged files. Verify the hook does not rewrite unrelated work.

Qlty spans local and CI gates; the full baseline, including why Qlty Cloud
checks stay non-required, is in the Qlty subsection of section 4.

## 3. Protect `main`

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

### Qlty

Qlty appears in three places. The committed configuration and the repository-run
gates are the alignment surface; the Qlty Cloud GitHub App is not.

- [ ] Commit `.qlty/qlty.toml` (`config_version = "0"`) with the organization
  exclude baseline for generated, minified, and vendored paths. Drydock's file
  is the reference posture and Portwing's is its Go-repo mirror; start from the
  closer of the two. Do not copy the trivy plugin block from either reference:
  trivy is deprecated in favor of Grype and its removal from both files is
  tracked (drydock#753, portwing#135). The trufflehog plugin stays. The same
  file drives the local CLI and Qlty Cloud, so changes to it are quality-gate
  changes, not formatting.
- [ ] Add the repository's Qlty gate script (`scripts/qlty-check-gate.sh all`)
  to `pre-push`, fail-fast like every other local gate. An advisory smells
  gate may run alongside it (Drydock pattern) but must not mask the gating
  script's failure.
- [ ] Gate Qlty in CI with the repository-run check, which needs no Qlty Cloud
  account. Go repositories call `go-ci.yml` with `run-qlty` enabled,
  `qlty-egress-policy: block`, and the proven endpoint allowlist — copy the
  allowlist from Portwing's `ci-verify.yml` rather than re-deriving it, and do
  not widen it. Node repositories run the SHA-pinned
  `qltysh/qlty-action/install` plus the same gate script inside their own
  workflow (Drydock pattern).

The Qlty Cloud GitHub App's statuses (`qlty check`, `qlty coverage`,
`qlty coverage diff`) currently error organization-wide with out-of-minutes
billing failures. Treat them as non-gating: never add them to required status
checks, and do not chase these failures on PRs — the repository-run gate above
is the enforced check. Coverage reporting is Codecov's job (decided
2026-08-16): upload coverage to Codecov and carry its badge in the README.
The Qlty Cloud App and its maintainability badge stay installed, but its
coverage upload is not the coverage system and its checks stay non-required.
Qlty Cloud enrollment and usage data are not an organization-wide
prerequisite; do not make a Cloud upload or check required
until the repository is enrolled and the check has proved stable. Local Qlty
checks may still be stricter than this baseline.

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
- [ ] Open a feature-to-active-branch PR, then an active-branch-to-`main` PR.
  Use the branch selected in section 1. Wait for all CI and an explicit
  CodeRabbit review on both, address every actionable finding, and obtain the
  required non-author and code-owner approvals.
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
