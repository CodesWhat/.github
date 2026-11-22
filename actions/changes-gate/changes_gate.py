#!/usr/bin/env python3
"""Decide whether a pull request changed files matching a pattern list.

Everything comes from environment variables. The only thing ever written to
the log is counts and the decision, and the only thing ever written to
GITHUB_OUTPUT is run=true or run=false. Any doubt resolves to true.
"""
import json
import os
import re
import sys
import time
import urllib.request

PER_PAGE = 100
FILE_CAP = 3000
MAX_PAGES = FILE_CAP // PER_PAGE
REQUEST_TIMEOUT = 20
DEADLINE_SECONDS = 150


class Doubt(Exception):
    """Raised for anything that should resolve to run=true."""


def parse_patterns(text):
    includes, excludes = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            line = line[1:].strip()
            if line:
                excludes.append(glob_to_regex(line))
        else:
            includes.append(glob_to_regex(line))
    return includes, excludes


def glob_to_regex(pattern):
    out = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern.startswith("**", i):
                i += 2
                if i < n and pattern[i] == "/":
                    i += 1
                    out.append("(?:.*/)?")
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out), re.IGNORECASE | re.DOTALL)


def matches(name, includes, excludes):
    if not any(r.fullmatch(name) for r in includes):
        return False
    return not any(r.fullmatch(name) for r in excludes)


def fetch_page(url, auth):
    """GET url and return (parsed JSON, Link header or None)."""
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + auth,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "codeswhat-changes-gate",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        if response.status != 200:
            raise Doubt("status")
        return json.loads(response.read().decode("utf-8")), response.headers.get("Link")


LINK_PART = re.compile(r'\s*<([^<>\s]+)>\s*((?:;\s*[A-Za-z]+\s*=\s*(?:"[^"]*"|[^\s;,"]+)\s*)*)')
REL = re.compile(r';\s*rel\s*=\s*"?([^";,]*)"?', re.IGNORECASE)


def next_link(header):
    """Return the rel="next" URL, None if absent. Malformed raises Doubt."""
    if header is None or not header.strip():
        return None
    found = None
    pos = 0
    while pos < len(header):
        match = LINK_PART.match(header, pos)
        if not match:
            raise Doubt("link")
        rel = REL.search(match.group(2))
        if rel and "next" in rel.group(1).lower().split():
            found = match.group(1)
        pos = match.end()
        if pos < len(header):
            if header[pos] != ",":
                raise Doubt("link")
            pos += 1
    return found


def expected_count(api, repo, number, auth, fetch):
    data, _ = fetch("{0}/repos/{1}/pulls/{2}".format(api, repo, number), auth)
    count = data.get("changed_files") if isinstance(data, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise Doubt("count")
    if count >= FILE_CAP:
        raise Doubt("cap")
    return count


def list_names(api, repo, number, auth, fetch):
    expected = expected_count(api, repo, number, auth, fetch)
    names = []
    files = 0
    started = time.monotonic()
    url = "{0}/repos/{1}/pulls/{2}/files?per_page={3}".format(api, repo, number, PER_PAGE)
    for _ in range(MAX_PAGES + 1):
        if time.monotonic() - started > DEADLINE_SECONDS:
            raise Doubt("deadline")
        data, link = fetch(url, auth)
        if not isinstance(data, list):
            raise Doubt("shape")
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                raise Doubt("shape")
            files += 1
            names.append(item["filename"])
            previous = item.get("previous_filename")
            if previous is not None:
                if not isinstance(previous, str):
                    raise Doubt("shape")
                names.append(previous)
        if files >= FILE_CAP:
            raise Doubt("cap")
        url = next_link(link)
        if url is None:
            if files != expected:
                raise Doubt("incomplete")
            return names
        if len(data) < PER_PAGE or not url.startswith(api + "/"):
            raise Doubt("link")
    raise Doubt("cap")


def pull_number(env):
    try:
        with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
            number = json.load(handle)["pull_request"]["number"]
    except (KeyError, OSError, ValueError, TypeError):
        raise Doubt("event")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise Doubt("event")
    return number


def decide(env, fetch=fetch_page):
    """Return (run, examined, matched)."""
    if env.get("CHANGES_GATE_EVENT_NAME") not in ("pull_request", "pull_request_target"):
        return True, 0, 0
    includes, excludes = parse_patterns(env.get("CHANGES_GATE_PATTERNS", ""))
    if not includes:
        return True, 0, 0
    repo = env.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return True, 0, 0
    api = (env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    auth = env.get("CHANGES_GATE_AUTH", "")
    if not auth:
        return True, 0, 0
    try:
        number = pull_number(env)
        names = list_names(api, repo, number, auth, fetch)
    except Exception:
        return True, 0, 0
    matched = sum(1 for name in names if matches(name, includes, excludes))
    return matched > 0, len(names), matched


def write_output(path, run):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("run={0}\n".format("true" if run else "false"))


def main(env=None, fetch=fetch_page):
    env = os.environ if env is None else env
    try:
        run, examined, matched = decide(env, fetch)
    except Exception:
        run, examined, matched = True, 0, 0
    print("changes-gate: examined={0} matched={1} run={2}".format(
        examined, matched, "true" if run else "false"))
    write_output(env.get("GITHUB_OUTPUT"), run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
