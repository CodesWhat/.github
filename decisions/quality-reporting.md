# Quality Reporting

Last updated: 2026-08-13

## Decision

Keep each repository's native test tool. Normalize the result envelope, not the
runner:

- Drydock keeps Stryker and fast-check.
- Sockguard keeps Gremlins and Go fuzzing.
- Portwing keeps Gremlins and Go fuzzing.

Mutation stays advisory and absent from required PR checks. A scheduled or
manual mutation workflow must still fail when its telemetry is incomplete or a
tool crashes. Non-blocking means it does not gate a PR. It does not mean the
workflow may report success after losing results.

Long fuzzing succeeds only when every declared target completes its budget. A
crash, repeated boundary flake, setup failure, or missing target result makes
the workflow red.

## Evidence behind the decision

The 2026-08-01 Drydock mutation run completed at the workflow level while 13 of
27 Stryker shards failed. Its aggregate used `--allow-missing` and published an
81.79% score from the remaining 14 shards. The workflow status and numeric
badge therefore described a partial result as success.

Sockguard's existing 97.8% badge uses `killed / (killed + lived)`. Counting
timeout as detected and no-coverage as missed gives 93.91% for the same run.
Portwing has no aggregate badge; applying the same canonical definition to its
latest complete Gremlins run gives 77.31%, versus 81.98% under Gremlins'
efficacy definition. Native percentages are not comparable until their
denominators are explicit.

Sockguard inventories all 48 in-tree fuzzers and covers every target in at
least one tier. Portwing's `FuzzVerifyRequest` runs in primary CI and scheduled
long tiers. Drydock's five fast-check fuzz tests run as ordinary Vitest tests
with default run counts and have no scheduled long-fuzz workflow.

## Normalized envelope

Every matrix leg writes one `target-result.json`. An always-running aggregate
job validates the set and publishes `quality-report/v1` with:

- `repository`, `ref`, `sha`
- `run`: `id`, `attempt`, `event`, `url`, `started_at`
- `track`: `mutation` or `fuzz`
- `tool`: `mutation` permits `gremlins` and `stryker`; `fuzz` permits
  `fast-check` and `go-fuzz`
- `policy`: `advisory` or `signal`
- `completeness`: `expected`, `reported`, `complete`
- `outcome`: `passed`, `failed`, `crashed`, `flaked`, `cancelled`, `error`
- `targets`: the declared target names and their individual results
- track-specific `metrics`

`go-fuzz` identifies Go's native `go test -fuzz` engine, not the legacy
third-party go-fuzz tool.

The schema and dependency-free validator live under [`quality-report/v1`](../quality-report/v1).
Both target-result fragments and aggregate reports carry the exact
`quality-report/v1` version. Objects reject unknown fields, JSON parsing rejects
duplicate fields and non-finite numbers, and semantic validation recomputes
completeness, outcomes, and scores. Callers pass `expected_targets` as a
non-empty JSON array of unique target names.

The Python validator is authoritative for cross-field semantics that JSON
Schema cannot express, including unique and sorted target names, recomputed
completeness, outcomes, and aggregate metrics.

Mutation metrics are:

- `killed`, `timeout`, `survived`, `no_coverage`, `invalid`, `ignored`
- `detected = killed + timeout`
- `missed = survived + no_coverage`
- `canonical_score_pct = 100 * detected / (detected + missed)`, rounded
  half-up to two decimals, or `null` when the denominator is zero
- `tool_score_pct` and `tool_score_definition`

Each target also supplies the native numerator and denominator. The aggregate
weights those counts instead of averaging percentages, and rejects mixed native
score definitions. The native score remains available with its definition.
Only the canonical score is comparable across mutation tools.

Fuzz metrics include the declared budget, elapsed time, and executions or new
interesting inputs when the runner exposes them. Do not compare execution
counts between runners or tools. A failed, crashed, or flaked fuzz result must
retain a seed, path, or corpus reference for reproduction.

## Reusable workflow contract

Inputs:

- `track`
- `tool`
- `policy`
- `expected_targets`
- `result_artifact_pattern`, default `quality-result-*`
- `report_name`
- `retention_days`, default `90`
- `fail_on_incomplete`, always `true` for CodesWhat telemetry

Outputs:

- `report_artifact_name`
- `completeness`
- `expected_targets`
- `reported_targets`
- `outcome`
- `canonical_score_pct` for mutation reports

The aggregate artifact is named
`quality-report-<track>-<run_id>-<attempt>` and contains `report.json` plus
`summary.md`. Native reports and crash corpora remain separate artifacts.
Target result JSON is always uploaded; crash forensics remain failure-only.
Normalized aggregate artifacts use at least 90 days of retention.

The reusable workflow checks out its own contract at the exact called-workflow
commit via `job.workflow_repository` and `job.workflow_sha`. Product workflows
remain responsible for native tool adapters and uploading their target-result
artifacts.

## Status and badge semantics

- The aggregate job runs under `if: always()` and fails on an incomplete set,
  tool crash, parse error, invalid contract, or policy failure.
- Numeric mutation badges publish only a complete canonical score. An
  incomplete run displays `partial N/M`, never a subset score or stale score.
- Do not commit badge JSON to `main`. Bot commits bypass the dev-to-main flow
  and make telemetry part of source history.
- Use the native workflow-status badge as the stable shared badge. Put numeric
  scores and native dashboard links in the run summary and retained artifact
  until there is a non-source telemetry endpoint.
- Long-fuzz failures must retain the runner seed, path, or corpus needed to
  reproduce the failure.

## Migration order

1. Add and test the schema, validator, and reusable aggregate in
   `CodesWhat/.github`.
2. Canary Portwing. It has the smallest Gremlins and fuzz matrices. Keep
   `FuzzVerifyRequest` in a long tier and preserve its source-to-tier inventory
   contract.
3. Migrate Sockguard's Gremlins aggregation, retire its direct badge commit,
   and preserve its existing fuzz inventory contract.
4. Migrate Drydock's Stryker aggregate, remove partial score publication, then
   add a scheduled fast-check workflow with explicit run count and seed/path
   reproduction.
5. Manually dispatch each workflow on the exact active dev head before syncing
   it to `main`. Scheduled workflows exercise `main`, not the active dev branch.

Native runner adapters and product callers are intentionally outside this
foundation change. Each migration supplies and tests its own mapping from
native output into the target-result contract.

## Risks and controls

- The three first-of-month mutation runs compete for organization runner
  capacity. Stagger their schedules.
- Gremlins text parsing is version-sensitive. Pin the tool and test fixtures
  from real output.
- Stryker reports compile/runtime-invalid mutants and may lose shards. Preserve
  native categories and fail closed on a missing shard.
- Fast-check failures are not reproducible without seed and path. Make both
  mandatory failure fields.
- Artifacts expire. Ninety days is the minimum retention for normalized
  reports; durable trend storage is a separate decision.
- `continue-on-error` on a matrix leg must not make the final aggregate green.
