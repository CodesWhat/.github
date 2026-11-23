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


STAR = ("*",)
ANY = ("?",)


def variants(ch):
    """Single-character case variants of ch. Multi-character expansions
    (e.g. the casefold of the German sharp s) are dropped."""
    return {v for v in (ch, ch.lower(), ch.upper(), ch.casefold()) if len(v) == 1}


def normalize(line):
    while line.startswith("./"):
        line = line[2:]
    if line.endswith("/"):
        line += "**"
    return line


def parse_patterns(text):
    """Return (includes, excludes). Raises ValueError on an unparseable glob.

    Includes match case-insensitively (over-triggering is safe). Excludes are
    exact: a case-folded exclude could hide a file the author never meant.
    """
    includes, excludes = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        target, fold = includes, True
        if line.startswith("!"):
            line = line[1:].strip()
            target, fold = excludes, False
        line = normalize(line)
        if line:
            target.append(Glob(line, fold))
    return includes, excludes


def char_class(pattern, start):
    """Parse a [...] class opening at start. Return (token, next index)."""
    n = len(pattern)
    i = start + 1
    negate = False
    if i < n and pattern[i] in "!^":
        negate = True
        i += 1
    ranges = []
    first = True
    while True:
        if i >= n:
            raise ValueError("unclosed class")
        c = pattern[i]
        if c == "]" and not first:
            i += 1
            break
        first = False
        if i + 2 < n and pattern[i + 1] == "-" and pattern[i + 2] != "]":
            lo, hi = c, pattern[i + 2]
            if lo > hi:
                raise ValueError("bad range")
            ranges.append((lo, hi))
            i += 3
        else:
            ranges.append((c, c))
            i += 1
    return ("[", negate, tuple(ranges)), i


def parse_segment(segment):
    """Turn one path segment of a glob into tokens."""
    tokens = []
    i = 0
    n = len(segment)
    while i < n:
        c = segment[i]
        if c == "*":
            if not tokens or tokens[-1] != STAR:
                tokens.append(STAR)
        elif c == "?":
            tokens.append(ANY)
        elif c == "[":
            token, i = char_class(segment, i)
            tokens.append(token)
            continue
        else:
            tokens.append(("c", c, frozenset(variants(c))))
        i += 1
    return tokens


def token_matches(token, ch, fold):
    kind = token[0]
    if kind == "?":
        return True
    if kind == "c":
        return ch == token[1] or (fold and not variants(ch).isdisjoint(token[2]))
    seen = variants(ch) if fold else {ch}
    hit = False
    for lo, hi in token[2]:
        if fold and lo == hi:
            hit = not seen.isdisjoint(variants(lo))
        else:
            hit = any(lo <= v <= hi for v in seen)
        if hit:
            break
    return hit != token[1]


def match_segment(tokens, text, fold):
    """Wildcard match of one segment. Worst case O(len(text) * len(tokens))."""
    ti = si = 0
    star = -1
    mark = 0
    count = len(tokens)
    while si < len(text):
        if ti < count and tokens[ti] == STAR:
            star = ti
            mark = si
            ti += 1
        elif ti < count and tokens[ti] != STAR and token_matches(tokens[ti], text[si], fold):
            ti += 1
            si += 1
        elif star >= 0:
            ti = star + 1
            mark += 1
            si = mark
        else:
            return False
    while ti < count and tokens[ti] == STAR:
        ti += 1
    return ti == count


class Glob:
    """A compiled glob. ** is a whole segment matching one or more (trailing)
    or zero or more (elsewhere) path segments."""

    def __init__(self, pattern, fold):
        if "{" in pattern or "}" in pattern or pattern.startswith("/") or "[[:" in pattern:
            raise ValueError("unsupported glob syntax")
        self.fold = fold
        segments = []
        for part in pattern.split("/"):
            if part == "**":
                if not segments or segments[-1] is not None:
                    segments.append(None)
            elif "**" in part:
                raise ValueError("embedded globstar")
            else:
                segments.append(parse_segment(part))
        if segments[-1] is None:
            segments.append([STAR])
        self.segments = segments

    def fullmatch(self, name):
        segs = self.segments
        parts = name.split("/")

        def close(states):
            for i in sorted(states):
                if i < len(segs) and segs[i] is None:
                    states.add(i + 1)
            return states

        states = close({0})
        for part in parts:
            nxt = set()
            for i in states:
                if i >= len(segs):
                    continue
                if segs[i] is None:
                    nxt.add(i)
                elif match_segment(segs[i], part, self.fold):
                    nxt.add(i + 1)
            if not nxt:
                return False
            states = close(nxt)
        return len(segs) in states


def matches(name, includes, excludes):
    if not any(r.fullmatch(name) for r in includes):
        return False
    return not any(r.fullmatch(name) for r in excludes)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the Authorization header must not leave the API."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


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
    with OPENER.open(request, timeout=REQUEST_TIMEOUT) as response:
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


def expected_count(api, repo, number, auth, fetch, head_sha):
    data, _ = fetch("{0}/repos/{1}/pulls/{2}".format(api, repo, number), auth)
    head = data.get("head") if isinstance(data, dict) else None
    current = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(current, str) or current.lower() != head_sha:
        raise Doubt("head")
    count = data.get("changed_files") if isinstance(data, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise Doubt("count")
    if count >= FILE_CAP:
        raise Doubt("cap")
    return count


def list_names(api, repo, number, auth, fetch, head_sha):
    expected = expected_count(api, repo, number, auth, fetch, head_sha)
    names = list_pages(api, repo, number, auth, fetch, expected)
    # A force-push between the metadata read and the listing can swap the
    # head under us, so the metadata has to be unchanged afterwards.
    if expected_count(api, repo, number, auth, fetch, head_sha) != expected:
        raise Doubt("moved")
    return names


def list_pages(api, repo, number, auth, fetch, expected):
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


def pull_request(env):
    """Return (number, head sha) from the event payload."""
    try:
        with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
            pull = json.load(handle)["pull_request"]
        number = pull["number"]
        sha = pull["head"]["sha"]
    except (KeyError, OSError, ValueError, TypeError):
        raise Doubt("event")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise Doubt("event")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise Doubt("event")
    return number, sha.lower()


def decide(env, fetch=fetch_page):
    """Return (run, examined, matched)."""
    if env.get("CHANGES_GATE_EVENT_NAME") not in ("pull_request", "pull_request_target"):
        return True, 0, 0
    try:
        includes, excludes = parse_patterns(env.get("CHANGES_GATE_PATTERNS", ""))
    except (ValueError, re.error):
        return True, 0, 0
    if not includes:
        return True, 0, 0
    repo = env.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(
            part in (".", "..") for part in repo.split("/")):
        return True, 0, 0
    api = (env.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    auth = env.get("CHANGES_GATE_AUTH", "")
    if not auth:
        return True, 0, 0
    try:
        number, head_sha = pull_request(env)
        names = list_names(api, repo, number, auth, fetch, head_sha)
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
