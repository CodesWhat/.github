#!/usr/bin/env python3
"""
Generate NFO-style SVG files for CodesWhat? org profile README.
Creates dark_mode.svg and light_mode.svg with embedded stats.
"""

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def escape_xml(s):
    """Escape XML special characters."""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def format_number(n):
    """Format number with suffix."""
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    elif n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def run_gh_api(endpoint):
    """Run a gh api command and return JSON result."""
    try:
        result = subprocess.run(
            ['gh', 'api', endpoint],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def run_gh_graphql(query, variables=None):
    """Run a gh api graphql query and return JSON result."""
    try:
        cmd = ['gh', 'api', 'graphql', '-f', f'query={query}']
        if variables:
            for key, value in variables.items():
                cmd.extend(['-F', f'{key}={value}'])
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def load_stats_cache():
    """Load cached stats."""
    cache_file = Path(__file__).parent / "cache" / "stats.json"
    if cache_file.exists():
        try:
            with open(cache_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def save_stats_cache(stats):
    """Save stats to cache file."""
    cache_dir = Path(__file__).parent / "cache"
    cache_dir.mkdir(exist_ok=True)
    cache_data = {
        'timestamp': datetime.now().isoformat(),
        'org': stats,
    }
    with open(cache_dir / "stats.json", 'w') as f:
        json.dump(cache_data, f, indent=2)


def merge_with_cache(live, cached):
    """Merge live stats with cached — cumulative values only go up."""
    if not cached:
        return live
    merged = dict(live)
    for key in ('total_commits', 'total_prs', 'total_issues',
                'loc_added', 'loc_deleted', 'loc_total'):
        if key in cached:
            merged[key] = max(merged.get(key, 0), cached[key])
    return merged


def get_org_stats(org="CodesWhat"):
    """Fetch org-level GitHub stats."""
    import time

    # Get org repos
    repos = run_gh_api(f'orgs/{org}/repos?per_page=100&type=all')
    if not repos or not isinstance(repos, list):
        cached = load_stats_cache()
        if cached and 'org' in cached:
            return cached['org']
        return {
            'public_repos': 0, 'private_repos': 0, 'total_repos': 0,
            'total_stars': 0, 'total_forks': 0, 'total_commits': 0,
            'total_prs': 0, 'total_issues': 0, 'members': 0,
            'loc_added': 0, 'loc_deleted': 0, 'loc_total': 0,
            'languages': [],
        }

    public_repos = sum(1 for r in repos if not r.get('private'))
    private_repos = sum(1 for r in repos if r.get('private'))
    total_stars = sum(r.get('stargazers_count', 0) for r in repos)
    total_forks = sum(r.get('forks_count', 0) for r in repos)

    # Collect languages across all repos
    lang_set = set()
    for repo in repos:
        lang = repo.get('language')
        if lang:
            lang_set.add(lang)

    # Get member count
    members_data = run_gh_api(f'orgs/{org}/members?per_page=100')
    member_count = len(members_data) if members_data and isinstance(members_data, list) else 0

    # Get total commits, PRs, issues across repos
    total_commits = 0
    total_prs = 0
    total_issues = 0
    total_added = 0
    total_deleted = 0

    for repo in repos:
        repo_name = repo['full_name']

        # Commits (default branch)
        commits = run_gh_api(f'repos/{repo_name}/commits?per_page=1')
        if commits and isinstance(commits, list):
            # Use the search API trick to get total count
            pass

        # Get contributor stats for LOC
        for attempt in range(3):
            contributors = run_gh_api(f'repos/{repo_name}/stats/contributors')
            if contributors is None:
                time.sleep(1)
                continue
            if isinstance(contributors, list):
                for contrib in contributors:
                    for week in contrib.get('weeks', []):
                        total_added += week.get('a', 0)
                        total_deleted += week.get('d', 0)
                        total_commits += week.get('c', 0)
                break

        # PRs and issues count
        prs = run_gh_api(f'repos/{repo_name}/pulls?state=all&per_page=1')
        if prs and isinstance(prs, list):
            # Check link header for total — fall back to listing
            pass

        issues = run_gh_api(f'repos/{repo_name}/issues?state=all&per_page=1&filter=all')
        if issues and isinstance(issues, list):
            pass

    # Use GraphQL for accurate PR/issue counts
    for repo in repos:
        owner = repo['owner']['login']
        name = repo['name']
        query = """
        query($owner: String!, $name: String!) {
            repository(owner: $owner, name: $name) {
                pullRequests { totalCount }
                issues { totalCount }
            }
        }
        """
        data = run_gh_graphql(query, {'owner': owner, 'name': name})
        if data and 'data' in data and data['data'].get('repository'):
            r = data['data']['repository']
            total_prs += r['pullRequests']['totalCount']
            total_issues += r['issues']['totalCount']

    live_stats = {
        'public_repos': public_repos,
        'private_repos': private_repos,
        'total_repos': len(repos),
        'total_stars': total_stars,
        'total_forks': total_forks,
        'total_commits': total_commits,
        'total_prs': total_prs,
        'total_issues': total_issues,
        'members': member_count,
        'loc_added': total_added,
        'loc_deleted': total_deleted,
        'loc_total': total_added - total_deleted,
        'languages': sorted(lang_set),
    }

    cached = load_stats_cache()
    return merge_with_cache(live_stats, cached.get('org') if cached else None)


def generate_svg(mode="dark", stats=None):
    """Generate SVG content for the given color mode."""

    if stats is None:
        stats = get_org_stats()

    # Color schemes
    if mode == "dark":
        colors = {
            'bg': '#0d1117',
            'text': '#39c5cf',
            'gray': '#8b949e',
            'magenta': '#e879f9',
            'green': '#a3e635',
            'red': '#f87171',
            'orange': '#fb923c',
            'yellow': '#fbbf24',
        }
    else:
        colors = {
            'bg': '#f6f8fa',
            'text': '#0969da',
            'gray': '#57606a',
            'magenta': '#c026d3',
            'green': '#65a30d',
            'red': '#dc2626',
            'orange': '#ea580c',
            'yellow': '#d97706',
        }

    c = colors
    width = 800
    font_size = 14
    line_height_normal = 20
    y_start = 30
    content_width = 90

    def stat_line(key, value):
        key_str = f"{key}:"
        val_str = str(value)
        dots = '.' * (content_width - len(key_str) - len(val_str) - 2)
        return f"{key_str} {dots} {val_str}"

    lines = []

    # Header art - question mark (fits the "CodesWhat?" brand)
    header_art = [
        "                                                                                          ",
        "                                     **********************                               ",
        "                                  ****************************                            ",
        "                                ****     ******************  ****                          ",
        "                               ***          ************       ***                         ",
        "                               ***           **********        ***                         ",
        "                               ***            ********         ***                         ",
        "                                ****          ********       ****                          ",
        "                                  ****       ********     ****                             ",
        "                                    ****    ********    ****                               ",
        "                                      ***  ********   ***                                  ",
        "                                       *** ******** ***                                    ",
        "                                        ** ******** **                                     ",
        "                                           ********                                        ",
        "                                           ********                                        ",
        "                                            ******                                         ",
        "                                             ****                                          ",
        "                                                                                          ",
        "                                             ****                                          ",
        "                                            ******                                         ",
        "                                             ****                                          ",
        "                                                                                          ",
    ]
    for art in header_art:
        lines.append((art, "yellow", "normal"))

    lines.append(("", "text", "blank"))
    lines.append(("C o d e s W h a t ?", "text", "normal"))
    lines.append(("", "text", "blank"))
    lines.append(("building things that make you go 'huh, neat'", "gray", "normal"))
    lines.append(("", "text", "blank"))
    lines.append(("", "text", "blank"))

    # Org Info
    lines.append(("---------------------------------------- ORG INFO ----------------------------------------", "orange", "normal"))
    lines.append(("", "text", "blank"))

    lang_str = ", ".join(stats.get('languages', [])) or "Various"
    for key, val in [
        ("Members", stats.get('members', 0)),
        ("Languages", lang_str),
        ("Focus", "AI Tools, Developer Experience, Open Source"),
    ]:
        lines.append((stat_line(key, val), "gray", "normal"))

    lines.append(("", "text", "blank"))
    lines.append(("", "text", "blank"))

    # Org Stats
    lines.append(("--------------------------------------- ORG STATS ----------------------------------------", "green", "normal"))
    lines.append(("", "text", "blank"))

    for key, val in [
        ("Repositories", f"{stats.get('public_repos', 0)} public / {stats.get('private_repos', 0)} private"),
        ("Total Stars", stats.get('total_stars', 0)),
        ("Total Forks", stats.get('total_forks', 0)),
        ("Total Commits", format_number(stats.get('total_commits', 0))),
        ("Pull Requests", stats.get('total_prs', 0)),
    ]:
        lines.append((stat_line(key, val), "gray", "normal"))

    # LOC line with colored +/-
    loc_total = stats.get('loc_total', 0)
    loc_added = stats.get('loc_added', 0)
    loc_deleted = stats.get('loc_deleted', 0)
    loc_value = f"{loc_total:,} ( +{loc_added:,}, -{loc_deleted:,} )"
    lines.append((f"__LOC__{loc_value}__", "loc", "normal"))

    lines.append(("", "text", "blank"))
    lines.append(("", "text", "blank"))

    # Projects
    lines.append(("--------------------------------------- PROJECTS -----------------------------------------", "magenta", "normal"))
    lines.append(("", "text", "blank"))
    lines.append((stat_line("whatsupdocker-ce", "container image update monitoring"), "gray", "normal"))
    lines.append((stat_line("smithers", "secure self-hosted AI assistant"), "gray", "normal"))
    lines.append((stat_line("mylair", "social platform for AI agents"), "gray", "normal"))
    lines.append(("", "text", "blank"))

    # Footer
    lines.append(("// CodesWhat? codes what needs coding.", "gray", "normal"))
    lines.append(("", "text", "blank"))
    lines.append((f"Last Updated: {datetime.now().strftime('%Y-%m-%d')}", "gray", "normal"))

    # Calculate height
    total_height = y_start
    for _, _, spacing in lines:
        if spacing == "blank":
            total_height += line_height_normal // 2
        else:
            total_height += line_height_normal
    height = total_height + 30

    # Build SVG
    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<style>
@font-face {{
    src: local('Consolas'), local('Monaco'), local('Menlo');
    font-family: 'MonoFallback';
    font-display: swap;
}}
text {{
    font-family: 'MonoFallback', ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
    font-size: {font_size}px;
    white-space: pre;
    dominant-baseline: text-before-edge;
}}
.text {{ fill: {c['text']}; }}
.gray {{ fill: {c['gray']}; }}
.magenta {{ fill: {c['magenta']}; }}
.green {{ fill: {c['green']}; }}
.red {{ fill: {c['red']}; }}
.orange {{ fill: {c['orange']}; }}
.yellow {{ fill: {c['yellow']}; }}
</style>
<rect width="{width}" height="{height}" fill="{c['bg']}" rx="10"/>
'''

    y = y_start
    for line_text, color, spacing in lines:
        if line_text.startswith('__LOC__'):
            loc_value = line_text.replace('__LOC__', '').replace('__', '')
            key_str = "Lines of Code:"
            dots_len = content_width - len(key_str) - len(loc_value) - 2
            dots = '.' * max(dots_len, 3)

            match = re.search(r'([\d,]+) \( \+([\d,]+), -([\d,]+) \)', loc_value)
            if match:
                total, added, deleted = match.groups()
                char_width = font_size * 0.6
                full_line = f"{key_str} {dots} {total} ( +{added}, -{deleted} )"
                line_width = len(full_line) * char_width
                start_x = (width - line_width) / 2

                prefix = f"{key_str} {dots} {total} ( "
                svg += f'<text x="{start_x}" y="{y}" class="gray">{prefix}</text>\n'

                green_x = start_x + len(prefix) * char_width
                green_text = f"+{added}"
                svg += f'<text x="{green_x}" y="{y}" class="green">{green_text}</text>\n'

                comma_x = green_x + len(green_text) * char_width
                svg += f'<text x="{comma_x}" y="{y}" class="gray">, </text>\n'

                red_x = comma_x + 2 * char_width
                red_text = f"-{deleted}"
                svg += f'<text x="{red_x}" y="{y}" class="red">{red_text}</text>\n'

                suffix_x = red_x + len(red_text) * char_width
                svg += f'<text x="{suffix_x}" y="{y}" class="gray"> )</text>\n'
            else:
                svg += f'<text x="{width // 2}" y="{y}" text-anchor="middle" class="gray">{key_str} {dots} {loc_value}</text>\n'
        elif line_text:
            escaped = escape_xml(line_text)
            svg += f'<text x="{width // 2}" y="{y}" text-anchor="middle" class="{color}">{escaped}</text>\n'

        if spacing == "blank":
            y += line_height_normal // 2
        else:
            y += line_height_normal

    svg += '</svg>'
    return svg


def main():
    """Generate both dark and light mode SVGs."""
    script_dir = Path(__file__).parent

    stats = get_org_stats()
    save_stats_cache(stats)

    # Generate dark mode
    dark_svg = generate_svg("dark", stats=stats)
    dark_path = script_dir / "profile" / "dark_mode.svg"
    with open(dark_path, 'w', encoding='utf-8') as f:
        f.write(dark_svg)
    print(f"Generated: {dark_path}")

    # Generate light mode
    light_svg = generate_svg("light", stats=stats)
    light_path = script_dir / "profile" / "light_mode.svg"
    with open(light_path, 'w', encoding='utf-8') as f:
        f.write(light_svg)
    print(f"Generated: {light_path}")


if __name__ == "__main__":
    main()
