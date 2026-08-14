#!/usr/bin/env python3
"""Validate and aggregate CodesWhat quality-report/v1 result envelopes."""

import argparse
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlparse


VERSION = "quality-report/v1"
KINDS = {"target-result", "report"}
TRACK_TOOLS = {
    "mutation": {"gremlins", "stryker"},
    "fuzz": {"fast-check", "go-fuzz"},
}
POLICIES = {"advisory", "signal"}
OUTCOMES = {"passed", "failed", "crashed", "flaked", "cancelled", "error"}
OUTCOME_PRIORITY = ("error", "crashed", "cancelled", "flaked", "failed")
MUTATION_COUNT_FIELDS = (
    "killed",
    "timeout",
    "survived",
    "no_coverage",
    "invalid",
    "ignored",
)
TARGET_MUTATION_FIELDS = set(MUTATION_COUNT_FIELDS) | {
    "tool_score_numerator",
    "tool_score_denominator",
    "tool_score_pct",
    "tool_score_definition",
}
REPORT_MUTATION_FIELDS = TARGET_MUTATION_FIELDS | {
    "detected",
    "missed",
    "canonical_score_pct",
}
TARGET_FUZZ_REQUIRED = {"budget_seconds", "elapsed_seconds"}
TARGET_FUZZ_OPTIONAL = {"executions", "new_interesting_inputs"}
REPORT_FUZZ_FIELDS = {
    "declared_budget_seconds",
    "elapsed_seconds",
    "executions",
    "new_interesting_inputs",
}
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)


class ContractError(ValueError):
    pass


def reject_constant(value):
    raise ContractError("non-finite JSON number: {0}".format(value))


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON field: {0}".format(key))
        result[key] = value
    return result


def load_json_text(text, source):
    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, ContractError) as error:
        raise ContractError("{0}: invalid JSON: {1}".format(source, error)) from error


def load_json_file(path):
    try:
        return load_json_text(path.read_text(encoding="utf-8"), str(path))
    except (OSError, UnicodeDecodeError) as error:
        raise ContractError(
            "{0}: cannot read JSON: {1}".format(path, error)
        ) from error


def require_object(value, path, required, optional=()):
    if not isinstance(value, dict):
        raise ContractError("{0}: expected object".format(path))
    required = set(required)
    allowed = required | set(optional)
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        raise ContractError(
            "{0}: missing field(s): {1}".format(path, ", ".join(missing))
        )
    if unknown:
        raise ContractError(
            "{0}: unknown field(s): {1}".format(path, ", ".join(unknown))
        )
    return value


def require_string(value, path, *, pattern=None, max_length=None):
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise ContractError("{0}: expected non-empty single-line string".format(path))
    if max_length is not None and len(value) > max_length:
        raise ContractError(
            "{0}: expected at most {1} characters".format(path, max_length)
        )
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ContractError("{0}: invalid value".format(path))
    return value


def require_enum(value, path, choices):
    require_string(value, path)
    if value not in choices:
        raise ContractError(
            "{0}: expected one of {1}".format(path, ", ".join(sorted(choices)))
        )
    return value


def require_integer(value, path, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError("{0}: expected integer >= {1}".format(path, minimum))
    return value


def require_number(value, path, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError("{0}: expected number >= {1}".format(path, minimum))
    if not math.isfinite(value) or value < minimum:
        raise ContractError("{0}: expected finite number >= {1}".format(path, minimum))
    return value


def require_nullable_integer(value, path):
    if value is not None:
        require_integer(value, path)


def require_percentage(value, path, *, nullable=False):
    if value is None and nullable:
        return
    require_number(value, path)
    if value > 100:
        raise ContractError("{0}: expected percentage <= 100".format(path))


def require_url(value, path):
    require_string(value, path)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ContractError("{0}: expected HTTPS URL".format(path))


def require_timestamp(value, path):
    require_string(value, path)
    try:
        if RFC3339_PATTERN.fullmatch(value) is None:
            raise ValueError
        datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError as error:
        raise ContractError(
            "{0}: expected RFC 3339 timestamp".format(path)
        ) from error


def percentage(numerator, denominator):
    if denominator == 0:
        return None
    value = Decimal(numerator) * Decimal(100) / Decimal(denominator)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_mutation_target_metrics(metrics, path):
    require_object(metrics, path, TARGET_MUTATION_FIELDS)
    for field in MUTATION_COUNT_FIELDS:
        require_integer(metrics[field], "{0}.{1}".format(path, field))
    numerator = require_integer(
        metrics["tool_score_numerator"], path + ".tool_score_numerator"
    )
    denominator = require_integer(
        metrics["tool_score_denominator"], path + ".tool_score_denominator"
    )
    require_percentage(
        metrics["tool_score_pct"], path + ".tool_score_pct", nullable=True
    )
    require_string(metrics["tool_score_definition"], path + ".tool_score_definition")
    expected = percentage(numerator, denominator)
    if metrics["tool_score_pct"] != expected:
        raise ContractError(
            "{0}.tool_score_pct: expected {1} from numerator/denominator".format(
                path, expected
            )
        )


def validate_fuzz_target_metrics(metrics, path):
    require_object(metrics, path, TARGET_FUZZ_REQUIRED, TARGET_FUZZ_OPTIONAL)
    require_integer(metrics["budget_seconds"], path + ".budget_seconds", minimum=1)
    require_number(metrics["elapsed_seconds"], path + ".elapsed_seconds")
    for field in TARGET_FUZZ_OPTIONAL:
        if field in metrics:
            require_integer(metrics[field], "{0}.{1}".format(path, field))


def validate_reproduction(value, path):
    require_object(value, path, (), {"seed", "path", "corpus"})
    if not value:
        raise ContractError("{0}: expected seed, path, or corpus".format(path))
    for field, item in value.items():
        require_string(item, "{0}.{1}".format(path, field))


def validate_target(target, track, path):
    require_object(
        target,
        path,
        {"name", "outcome", "metrics"},
        {"diagnostic", "reproduction"},
    )
    require_string(target["name"], path + ".name", max_length=256)
    outcome = require_enum(target["outcome"], path + ".outcome", OUTCOMES)
    metrics = target["metrics"]
    if metrics is None:
        if outcome == "passed":
            raise ContractError(
                "{0}.metrics: passed target requires metrics".format(path)
            )
    elif track == "mutation":
        validate_mutation_target_metrics(metrics, path + ".metrics")
    else:
        validate_fuzz_target_metrics(metrics, path + ".metrics")
        if (
            outcome == "passed"
            and metrics["elapsed_seconds"] < metrics["budget_seconds"]
        ):
            raise ContractError(
                "{0}.metrics.elapsed_seconds: passed target has not completed fuzz budget".format(
                    path
                )
            )

    if outcome == "passed":
        if "diagnostic" in target or "reproduction" in target:
            raise ContractError(
                "{0}: passed target cannot contain diagnostic or reproduction".format(
                    path
                )
            )
    else:
        if "diagnostic" not in target:
            raise ContractError(
                "{0}: non-passed target requires diagnostic".format(path)
            )
        require_string(target["diagnostic"], path + ".diagnostic")
        if track == "fuzz" and outcome in {"failed", "crashed", "flaked"}:
            if "reproduction" not in target:
                raise ContractError(
                    "{0}: failed fuzz target requires reproduction".format(path)
                )
        if "reproduction" in target:
            validate_reproduction(target["reproduction"], path + ".reproduction")


def validate_target_document(document, path="$"):
    require_object(
        document,
        path,
        {"schema_version", "kind", "track", "tool", "target"},
    )
    if document["schema_version"] != VERSION:
        raise ContractError("{0}.schema_version: expected {1}".format(path, VERSION))
    if document["kind"] != "target-result":
        raise ContractError("{0}.kind: expected target-result".format(path))
    track = require_enum(document["track"], path + ".track", set(TRACK_TOOLS))
    tool = require_enum(
        document["tool"], path + ".tool", set().union(*TRACK_TOOLS.values())
    )
    if tool not in TRACK_TOOLS[track]:
        raise ContractError("{0}.tool: {1} is not a {2} tool".format(path, tool, track))
    validate_target(document["target"], track, path + ".target")
    return document


def aggregate_mutation_metrics(targets):
    metrics = {field: 0 for field in MUTATION_COUNT_FIELDS}
    definitions = set()
    for target in targets:
        target_metrics = target["metrics"]
        for field in MUTATION_COUNT_FIELDS:
            metrics[field] += target_metrics[field]
        metrics.setdefault("tool_score_numerator", 0)
        metrics.setdefault("tool_score_denominator", 0)
        metrics["tool_score_numerator"] += target_metrics["tool_score_numerator"]
        metrics["tool_score_denominator"] += target_metrics["tool_score_denominator"]
        definitions.add(target_metrics["tool_score_definition"])
    if len(definitions) != 1:
        raise ContractError("target results use inconsistent native score definitions")
    metrics["detected"] = metrics["killed"] + metrics["timeout"]
    metrics["missed"] = metrics["survived"] + metrics["no_coverage"]
    metrics["canonical_score_pct"] = percentage(
        metrics["detected"], metrics["detected"] + metrics["missed"]
    )
    metrics["tool_score_pct"] = percentage(
        metrics["tool_score_numerator"], metrics["tool_score_denominator"]
    )
    metrics["tool_score_definition"] = definitions.pop()
    return metrics


def aggregate_fuzz_metrics(targets):
    metrics = {
        "declared_budget_seconds": sum(
            target["metrics"]["budget_seconds"] for target in targets
        ),
        "elapsed_seconds": sum(
            target["metrics"]["elapsed_seconds"] for target in targets
        ),
    }
    for field in ("executions", "new_interesting_inputs"):
        if all(field in target["metrics"] for target in targets):
            metrics[field] = sum(target["metrics"][field] for target in targets)
        else:
            metrics[field] = None
    return metrics


def derive_outcome(targets, errors):
    if errors:
        return "error"
    outcomes = {target["outcome"] for target in targets}
    for outcome in OUTCOME_PRIORITY:
        if outcome in outcomes:
            return outcome
    return "passed"


def validate_mutation_report_metrics(metrics, targets, path):
    require_object(metrics, path, REPORT_MUTATION_FIELDS)
    for field in MUTATION_COUNT_FIELDS + (
        "detected",
        "missed",
        "tool_score_numerator",
        "tool_score_denominator",
    ):
        require_integer(metrics[field], "{0}.{1}".format(path, field))
    require_percentage(
        metrics["canonical_score_pct"], path + ".canonical_score_pct", nullable=True
    )
    require_percentage(
        metrics["tool_score_pct"], path + ".tool_score_pct", nullable=True
    )
    require_string(metrics["tool_score_definition"], path + ".tool_score_definition")
    expected = aggregate_mutation_metrics(targets)
    if metrics != expected:
        raise ContractError(
            "{0}: aggregate mutation metrics do not match targets".format(path)
        )


def validate_fuzz_report_metrics(metrics, targets, path):
    require_object(metrics, path, REPORT_FUZZ_FIELDS)
    require_integer(
        metrics["declared_budget_seconds"],
        path + ".declared_budget_seconds",
        minimum=1,
    )
    require_number(metrics["elapsed_seconds"], path + ".elapsed_seconds")
    require_nullable_integer(metrics["executions"], path + ".executions")
    require_nullable_integer(
        metrics["new_interesting_inputs"], path + ".new_interesting_inputs"
    )
    expected = aggregate_fuzz_metrics(targets)
    if metrics != expected:
        raise ContractError(
            "{0}: aggregate fuzz metrics do not match targets".format(path)
        )


def validate_report(document, path="$"):
    require_object(
        document,
        path,
        {
            "schema_version",
            "kind",
            "repository",
            "ref",
            "sha",
            "run",
            "report_name",
            "track",
            "tool",
            "policy",
            "completeness",
            "outcome",
            "targets",
            "metrics",
            "errors",
        },
    )
    if document["schema_version"] != VERSION:
        raise ContractError("{0}.schema_version: expected {1}".format(path, VERSION))
    if document["kind"] != "report":
        raise ContractError("{0}.kind: expected report".format(path))
    require_string(document["repository"], path + ".repository")
    require_string(document["ref"], path + ".ref")
    require_string(document["sha"], path + ".sha", pattern=SHA_PATTERN)
    require_string(document["report_name"], path + ".report_name")
    track = require_enum(document["track"], path + ".track", set(TRACK_TOOLS))
    tool = require_enum(
        document["tool"], path + ".tool", set().union(*TRACK_TOOLS.values())
    )
    if tool not in TRACK_TOOLS[track]:
        raise ContractError("{0}.tool: {1} is not a {2} tool".format(path, tool, track))
    require_enum(document["policy"], path + ".policy", POLICIES)

    run = require_object(
        document["run"],
        path + ".run",
        {"id", "attempt", "event", "url", "started_at"},
    )
    require_string(run["id"], path + ".run.id")
    require_integer(run["attempt"], path + ".run.attempt", minimum=1)
    require_string(run["event"], path + ".run.event")
    require_url(run["url"], path + ".run.url")
    if run["started_at"] is not None:
        require_timestamp(run["started_at"], path + ".run.started_at")

    completeness = require_object(
        document["completeness"],
        path + ".completeness",
        {"expected", "reported", "complete"},
    )
    require_integer(
        completeness["expected"], path + ".completeness.expected", minimum=1
    )
    require_integer(completeness["reported"], path + ".completeness.reported")
    if not isinstance(completeness["complete"], bool):
        raise ContractError(path + ".completeness.complete: expected boolean")

    targets = document["targets"]
    if not isinstance(targets, list):
        raise ContractError(path + ".targets: expected array")
    names = []
    for index, target in enumerate(targets):
        validate_target(target, track, "{0}.targets[{1}]".format(path, index))
        names.append(target["name"])
    if len(names) != len(set(names)):
        raise ContractError(path + ".targets: duplicate target name")
    if names != sorted(names):
        raise ContractError(path + ".targets: targets must be sorted by name")
    if completeness["reported"] != len(targets):
        raise ContractError(path + ".completeness.reported: does not match targets")

    errors = document["errors"]
    if not isinstance(errors, list) or any(
        not isinstance(error, str) or not error for error in errors
    ):
        raise ContractError(path + ".errors: expected non-empty strings")
    if errors != sorted(set(errors)):
        raise ContractError(path + ".errors: errors must be sorted and unique")
    if run["started_at"] is None and not errors:
        raise ContractError(path + ".run.started_at: required for an error-free report")
    expected_complete = (
        completeness["expected"] == completeness["reported"] and not errors
    )
    if completeness["complete"] != expected_complete:
        raise ContractError(path + ".completeness.complete: inconsistent value")

    outcome = require_enum(document["outcome"], path + ".outcome", OUTCOMES)
    if outcome != derive_outcome(targets, errors):
        raise ContractError(path + ".outcome: does not match target results")

    metrics = document["metrics"]
    metrics_available = (
        completeness["complete"]
        and bool(targets)
        and all(target["metrics"] is not None for target in targets)
    )
    if not metrics_available:
        if metrics is not None:
            raise ContractError(path + ".metrics: must be null for partial results")
    elif track == "mutation":
        validate_mutation_report_metrics(metrics, targets, path + ".metrics")
    else:
        validate_fuzz_report_metrics(metrics, targets, path + ".metrics")
    return document


def validate_document(document):
    if not isinstance(document, dict):
        raise ContractError("$: expected object")
    kind = document.get("kind")
    if kind not in KINDS:
        raise ContractError("$.kind: expected one of report, target-result")
    if kind == "target-result":
        return validate_target_document(document)
    return validate_report(document)


def parse_expected_targets(raw):
    value = load_json_text(raw, "expected_targets")
    if not isinstance(value, list) or not value:
        raise ContractError("expected_targets: expected a non-empty JSON array")
    targets = []
    for index, target in enumerate(value):
        targets.append(
            require_string(
                target,
                "expected_targets[{0}]".format(index),
                max_length=256,
            )
        )
    if len(targets) != len(set(targets)):
        raise ContractError("expected_targets: duplicate target")
    return targets


def relative_label(path, root):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def collect_targets(input_dir, expected_targets, track, tool):
    errors = []
    reported = {}
    paths = sorted(input_dir.rglob("target-result.json")) if input_dir.is_dir() else []
    if not paths:
        errors.append("no target-result.json files found")
    for path in paths:
        label = relative_label(path, input_dir)
        try:
            document = load_json_file(path)
            validate_target_document(document, label)
            if document["track"] != track:
                raise ContractError(
                    "{0}.track: expected {1}, got {2}".format(
                        label, track, document["track"]
                    )
                )
            if document["tool"] != tool:
                raise ContractError(
                    "{0}.tool: expected {1}, got {2}".format(
                        label, tool, document["tool"]
                    )
                )
            target = document["target"]
            name = target["name"]
            if name not in expected_targets:
                raise ContractError("{0}: unexpected target: {1}".format(label, name))
            if name in reported:
                raise ContractError("{0}: duplicate target: {1}".format(label, name))
            reported[name] = target
        except ContractError as error:
            errors.append(str(error))
    for target in expected_targets:
        if target not in reported:
            errors.append("missing target: {0}".format(target))
    return [reported[name] for name in sorted(reported)], sorted(set(errors))


def build_summary(report):
    completeness = report["completeness"]
    if completeness["complete"]:
        completeness_text = "complete {0}/{1}".format(
            completeness["reported"], completeness["expected"]
        )
    else:
        completeness_text = "partial {0}/{1}".format(
            completeness["reported"], completeness["expected"]
        )
    lines = [
        "# {0}".format(report["report_name"]),
        "",
        "- Track: `{0}` via `{1}`".format(report["track"], report["tool"]),
        "- Policy: `{0}`".format(report["policy"]),
        "- Completeness: **{0}**".format(completeness_text),
        "- Outcome: **{0}**".format(report["outcome"]),
    ]
    metrics = report["metrics"]
    if report["track"] == "mutation" and metrics is not None:
        canonical = metrics["canonical_score_pct"]
        native = metrics["tool_score_pct"]
        if canonical is not None:
            lines.append("- Canonical score: **{0:.2f}%**".format(canonical))
        if native is not None:
            lines.append(
                "- Native score: **{0:.2f}%** ({1})".format(
                    native, metrics["tool_score_definition"]
                )
            )
    lines.extend(("", "## Targets", ""))
    for target in report["targets"]:
        detail = ""
        if "diagnostic" in target:
            detail = ": {0}".format(target["diagnostic"])
        lines.append(
            "- `{0}`: {1}{2}".format(target["name"], target["outcome"], detail)
        )
    if report["errors"]:
        lines.extend(("", "## Contract errors", ""))
        lines.extend("- {0}".format(error) for error in report["errors"])
    return "\n".join(lines) + "\n"


def append_lines(path, values):
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as output:
        for key, value in values.items():
            output.write("{0}={1}\n".format(key, value))


def command_aggregate(args):
    errors = []
    try:
        expected_targets = parse_expected_targets(args.expected_targets)
    except ContractError as error:
        expected_targets = ["invalid-expected-targets"]
        errors.append(str(error))
    if args.fail_on_incomplete != "true":
        errors.append("fail_on_incomplete must be true for CodesWhat telemetry")
    try:
        require_timestamp(args.started_at, "started_at")
        started_at = args.started_at
    except ContractError as error:
        started_at = None
        errors.append(str(error))

    targets, collection_errors = collect_targets(
        args.input_dir, expected_targets, args.track, args.tool
    )
    errors.extend(collection_errors)
    errors = sorted(set(errors))
    completeness = {
        "expected": len(expected_targets),
        "reported": len(targets),
        "complete": len(targets) == len(expected_targets) and not errors,
    }
    metrics = None
    if completeness["complete"] and all(
        target["metrics"] is not None for target in targets
    ):
        try:
            if args.track == "mutation":
                metrics = aggregate_mutation_metrics(targets)
            else:
                metrics = aggregate_fuzz_metrics(targets)
        except ContractError as error:
            errors.append(str(error))
            errors = sorted(set(errors))
            completeness["complete"] = False

    report = {
        "schema_version": VERSION,
        "kind": "report",
        "repository": args.repository,
        "ref": args.ref,
        "sha": args.sha,
        "run": {
            "id": args.run_id,
            "attempt": args.run_attempt,
            "event": args.event,
            "url": args.run_url,
            "started_at": started_at,
        },
        "report_name": args.report_name,
        "track": args.track,
        "tool": args.tool,
        "policy": args.policy,
        "completeness": completeness,
        "outcome": derive_outcome(targets, errors),
        "targets": targets,
        "metrics": metrics,
        "errors": errors,
    }
    validate_report(report)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(report)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    (args.output_dir / "summary.md").write_text(summary)
    append_lines(
        args.github_output,
        {
            "report_artifact_name": "quality-report-{0}-{1}-{2}".format(
                args.track, args.run_id, args.run_attempt
            ),
            "completeness": str(completeness["complete"]).lower(),
            "expected_targets": completeness["expected"],
            "reported_targets": completeness["reported"],
            "outcome": report["outcome"],
            "canonical_score_pct": (
                ""
                if metrics is None
                or args.track != "mutation"
                or metrics["canonical_score_pct"] is None
                else "{0:.2f}".format(metrics["canonical_score_pct"])
            ),
        },
    )
    if args.github_step_summary is not None:
        args.github_step_summary.parent.mkdir(parents=True, exist_ok=True)
        with args.github_step_summary.open("a") as step_summary:
            step_summary.write(summary)
    return 0 if completeness["complete"] and report["outcome"] == "passed" else 1


def command_validate(args):
    document = load_json_file(args.document)
    validate_document(document)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("document", type=Path)
    validate_parser.set_defaults(handler=command_validate)

    aggregate_parser = subparsers.add_parser("aggregate")
    aggregate_parser.add_argument("--input-dir", type=Path, required=True)
    aggregate_parser.add_argument("--output-dir", type=Path, required=True)
    aggregate_parser.add_argument("--repository", required=True)
    aggregate_parser.add_argument("--ref", required=True)
    aggregate_parser.add_argument("--sha", required=True)
    aggregate_parser.add_argument("--run-id", required=True)
    aggregate_parser.add_argument("--run-attempt", type=int, required=True)
    aggregate_parser.add_argument("--event", required=True)
    aggregate_parser.add_argument("--run-url", required=True)
    aggregate_parser.add_argument("--started-at", required=True)
    aggregate_parser.add_argument("--track", choices=sorted(TRACK_TOOLS), required=True)
    aggregate_parser.add_argument(
        "--tool", choices=sorted(set().union(*TRACK_TOOLS.values())), required=True
    )
    aggregate_parser.add_argument("--policy", choices=sorted(POLICIES), required=True)
    aggregate_parser.add_argument("--expected-targets", required=True)
    aggregate_parser.add_argument("--report-name", required=True)
    aggregate_parser.add_argument(
        "--fail-on-incomplete", choices=("true", "false"), required=True
    )
    aggregate_parser.add_argument("--github-output", type=Path)
    aggregate_parser.add_argument("--github-step-summary", type=Path)
    aggregate_parser.set_defaults(handler=command_aggregate)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        return args.handler(args)
    except ContractError as error:
        print("quality-report: {0}".format(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
