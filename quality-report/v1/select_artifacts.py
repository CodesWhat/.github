#!/usr/bin/env python3
"""Select the target-result artifacts produced by the current workflow attempt."""

import argparse
from datetime import datetime, timezone
import fnmatch
import json
import re
import subprocess
import sys

PAGE_SIZE = 100
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


EXTGLOB = re.compile(r"[@+!?*]\(")


class SelectionError(Exception):
    pass


def expand_braces(pattern):
    """Expand {a,b} alternatives the way minimatch does for download-artifact.

    Nested groups expand recursively. A group with no top-level comma, or an
    unbalanced brace, stays literal, which also matches minimatch.
    """
    depth = 0
    start = None
    for index, char in enumerate(pattern):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0:
                body = pattern[start + 1:index]
                options, level, last = [], 0, 0
                for position, inner in enumerate(body):
                    if inner == "{":
                        level += 1
                    elif inner == "}":
                        level -= 1
                    elif inner == "," and level == 0:
                        options.append(body[last:position])
                        last = position + 1
                if not options:
                    head, tail = pattern[:index + 1], pattern[index + 1:]
                    return [head + rest for rest in expand_braces(tail)]
                options.append(body[last:])
                prefix, suffix = pattern[:start], pattern[index + 1:]
                expanded = []
                for option in options:
                    for item in expand_braces(prefix + option + suffix):
                        if item not in expanded:
                            expanded.append(item)
                return expanded
    return [pattern]


def matches(name, pattern):
    return any(fnmatch.fnmatchcase(name, item) for item in expand_braces(pattern))


def gh_api(path):
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=False, timeout=60
    )
    if result.returncode != 0:
        raise SelectionError("gh api {0} failed: {1}".format(path, result.stderr.strip()))
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise SelectionError("gh api {0} returned invalid JSON".format(path)) from error


def parse_timestamp(value, label):
    if not isinstance(value, str) or not TIMESTAMP.match(value):
        raise SelectionError("{0}: missing or ambiguous timestamp: {1!r}".format(label, value))
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def list_artifacts(repository, run_id, fetch):
    artifacts = []
    page = 1
    while True:
        document = fetch(
            "/repos/{0}/actions/runs/{1}/artifacts?per_page={2}&page={3}".format(
                repository, run_id, PAGE_SIZE, page
            )
        )
        batch = document.get("artifacts") if isinstance(document, dict) else None
        if not isinstance(batch, list):
            raise SelectionError("artifact listing page {0} is malformed".format(page))
        artifacts.extend(batch)
        total = document.get("total_count")
        if len(batch) < PAGE_SIZE or (isinstance(total, int) and len(artifacts) >= total):
            return artifacts
        page += 1


def select_artifacts(repository, run_id, attempt, pattern, fetch=gh_api):
    if EXTGLOB.search(pattern):
        raise SelectionError(
            "unsupported extglob in artifact pattern: {0}".format(pattern)
        )
    attempt_run = fetch(
        "/repos/{0}/actions/runs/{1}/attempts/{2}".format(repository, run_id, attempt)
    )
    started_at = parse_timestamp(
        attempt_run.get("run_started_at") if isinstance(attempt_run, dict) else None,
        "attempt run_started_at",
    )
    selected = []
    for artifact in list_artifacts(repository, run_id, fetch):
        name = artifact.get("name")
        if not isinstance(name, str) or not matches(name, pattern):
            continue
        if artifact.get("expired") is True:
            continue
        created_at = parse_timestamp(
            artifact.get("created_at"), "artifact {0} created_at".format(name)
        )
        if created_at > started_at:
            artifact_id = artifact.get("id")
            if not isinstance(artifact_id, int) or isinstance(artifact_id, bool):
                raise SelectionError("artifact {0} has no integer id".format(name))
            selected.append(artifact_id)
    return sorted(set(selected))


def write_output(path, ids):
    if path is not None:
        with open(path, "a") as handle:
            handle.write("artifact_ids={0}\n".format(",".join(str(i) for i in ids)))


def main(argv=None, fetch=gh_api):
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)
    try:
        ids = select_artifacts(
            args.repository, args.run_id, args.run_attempt, args.pattern, fetch
        )
    except SelectionError as error:
        write_output(args.github_output, [])
        print("error: {0}".format(error), file=sys.stderr)
        return 1
    write_output(args.github_output, ids)
    print("selected {0} artifact(s) from attempt {1}".format(len(ids), args.run_attempt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
