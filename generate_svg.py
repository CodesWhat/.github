#!/usr/bin/env python3
"""
Generate SVG files for CodesWhat? org profile README.
Creates dark_mode.svg and light_mode.svg.
"""

import re
from pathlib import Path


def escape_xml(s):
    """Escape XML special characters."""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def generate_svg(mode="dark"):
    """Generate SVG content for the given color mode."""

    if mode == "dark":
        colors = {
            'bg': '#0d1117',
            'art_solid': '#c8ff00',    # lime hyper green for solid blocks
            'art_mid': '#5a7a00',      # muted green for shade/edge chars
            'art_dim': '#1a2600',      # dark green for ╬ fill
            'title': '#c8ff00',        # lime green
            'tagline': '#8b949e',      # muted gray
            'tagline2': '#6e7681',     # dimmer gray
        }
    else:
        colors = {
            'bg': '#ffffff',
            'art_solid': '#3300ff',    # deep electric blue for solid blocks
            'art_mid': '#8877cc',      # muted blue for shade/edge chars
            'art_dim': '#e0dff0',      # faint blue-gray for ╬ fill
            'title': '#3300ff',        # deep electric blue
            'tagline': '#555555',      # medium gray
            'tagline2': '#888888',     # lighter gray
        }

    c = colors
    width = 680
    font_size = 11
    art_line_height = 13
    text_line_height = 24
    y_start = 16

    logo_art = [
        "                             ╓▌██████████████████▌▄",
        "                        ╓██████████████████████████████▄",
        "                     ▓██████████╬╬▒╬╬╬╬╬╬╬╬╬╬╬╠▀██████████▌",
        "                  ▄██████▀▌╠╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬▒▀▀██████▌",
        "                ███████╠╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╠███████",
        "              ██████╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬▀█████",
        "            ╥█████╠╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬▄████████▌╬╬╬╬╬╬╠█████▌",
        "           █████▌╬╬╬╬╬╬▒███████████▒╬╬╬╬╬╬╬╠████████████▌╬╬╬╬╬╬╠█████",
        "          █████▒╬╬╬╬╬╬██████████████▌╬╬╬╬╬╬█████╬╠╠╣█████╬╬╬╬╬╬╬╬█████",
        "         █████╬╬╬╬╬╬╬█████▀╬╬╬╬▒█▀╬╬╬╬╬╬╬╬╬████╬╬╬╬╬█████╬╬╬╬╬╬╬╬╬█████",
        "        █████╬╬╬╬╬╬╬╟████▌╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╠██████╠╬╬╬╬╬╬╬╬╬╬╫████",
        "        ████▀╬╬╬╬╬╬╬█████▄╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬██████╪╬╬╬╬╬╬╬╬╬╬╬╬╬████▌",
        "       ████▌╬╬╬╬╬╬╬╬▒█████▒╬╬╬╬╬╬█╬╬╬╬╬╬╬╬╬╬╬╬╬╣████╪╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬█████",
        "       ████▌╬╬╬╬╬╬╬╬╬╬██████████████▌╬╬╬╬╬╬╬╬╬╬▒▓▓▓▓╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬████",
        "       ████╬╬╬╬╬╬╬╬╬╬╬╬████████████▌╬╬╬╬╬╬╬╬╬╬╬╣████╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬████▄",
        "       ████╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╠╣███▀╠╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬████╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬████▄",
        "       ████▌╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬████",
        "       ╫███▌╬╬╬╬╬╬╬╬╬╬▒█████╬╬╬╬╬╬╬╬╬█████▌╬╬╬╬╬╬╬╬Å█████╬╬╬╬╬╬╬╬╬╬╬╟████",
        "       ╙████▒╬╬╬╬╬╬╬╬╬╬╬█████╬╬╬╬╬╬╬███████▌╬╬╬╬╬╬╬█████╬╬╬╬╬╬╬╬╬╬╬╬████▌",
        "        █████╬╬╬╬╬╬╬╬╬╬╬▀████▌╬╬╬╬╬█████████▒╬╬╬╬╬█████▒╬╬╬╬╬╬╬╬╬╬╬█████",
        "         █████╬╬╬╬╬╬╬╬╬╬╬█████▌╬╬╬█████▀█████╬╬╬╬█████▀╬╬╬╬╬╬╬╬╬╬╬╟████b",
        "         └█████╬╬╬╬╬╬╬╬╬╬╬█████▒╬╟████▀╬▒█████╬╬█████▀╬╬╬╬╬╬╬╬╬╬╬▓████▀",
        "           █████▄╬╬╬╬╬╬╬╬╬▒█████╠████▌╬╬╬╠█████╟█████╬╬╬╬╬╬╬╬╬╬╬█████▀",
        "            ██████╬╬╬╬╬╬╬╬╬╠████████▌╬╬╬╬╬╠████████▌╬╬╬╬╬╬╬╬╬╬▓█████",
        "             ╙██████╬╬╬╬╬╬╬╬╬███████╬╬╬╬╬╬╬╠███████╬╬╬╬╬╬╬╬╬▓█████▀",
        "               ▀██████▌╬╬╬╬╬╬╬█████╬╬╬╬╬╬╬╬╬╠█████▒╬╬╬╬╬╬▄██████▀",
        "                 ╩▀██████▓╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╠███████▀",
        "                    ██████████▌╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╬╠▓████████▌└",
        "                       ╩███████████████████████████████▀▀",
        "                           ╙▀█▀███████████████████▀▀▀",
        "                                  └╙╙▀▀▀▀▀▀▀╙┴",
    ]

    # Build SVG with per-character coloring for depth
    lines = []
    for art in logo_art:
        lines.append(("art", art))
    lines.append(("blank", ""))
    lines.append(("blank", ""))
    lines.append(("title", "CodesWhat?"))
    lines.append(("blank", ""))
    lines.append(("tagline", "if you'd like to build better worlds together"))
    lines.append(("tagline2", "...we mean software... reach out"))

    # Calculate height
    total_height = y_start
    for typ, _ in lines:
        if typ == "art":
            total_height += art_line_height
        elif typ == "blank":
            total_height += text_line_height // 2
        else:
            total_height += text_line_height
    height = total_height + 24

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
    white-space: pre;
    dominant-baseline: text-before-edge;
}}
</style>
<rect width="{width}" height="{height}" fill="{c['bg']}" rx="10"/>
'''

    char_width = font_size * 0.6
    y = y_start

    # Art lines have ragged lengths; center the block as a whole off the
    # widest line so every line shares one left edge
    art_max_len = max(len(l) for l in logo_art)
    art_start_x = (width - art_max_len * char_width) / 2

    solid_chars = set('█▓▌▐▄▀')
    dim_chars = set('╬╠╣╟╫╪Å')

    def art_color(ch):
        if ch == ' ':
            return None
        if ch in solid_chars:
            return c['art_solid']
        if ch in dim_chars:
            return c['art_dim']
        return c['art_mid']

    for typ, content in lines:
        if typ == "art" and content.strip():
            # Render art with per-character color spans
            # Build runs of same-colored characters
            runs = []
            i = 0
            while i < len(content):
                color = art_color(content[i])
                if color is None:
                    i += 1
                    continue

                start = i
                while i < len(content) and art_color(content[i]) == color:
                    i += 1

                x = art_start_x + start * char_width
                text = escape_xml(content[start:i])
                runs.append(f'<text x="{x:.1f}" y="{y}" fill="{color}" font-size="{font_size}px">{text}</text>')

            svg += '\n'.join(runs) + '\n'
            y += art_line_height

        elif typ == "art":
            y += art_line_height

        elif typ == "title":
            svg += f'<text x="{width // 2}" y="{y}" text-anchor="middle" fill="{c["title"]}" font-size="22px" font-weight="600" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif">{escape_xml(content)}</text>\n'
            y += text_line_height

        elif typ == "tagline":
            svg += f'<text x="{width // 2}" y="{y}" text-anchor="middle" fill="{c["tagline"]}" font-size="13px" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif">{escape_xml(content)}</text>\n'
            y += text_line_height

        elif typ == "tagline2":
            svg += f'<text x="{width // 2}" y="{y}" text-anchor="middle" fill="{c["tagline2"]}" font-size="12px" font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif">{escape_xml(content)}</text>\n'
            y += text_line_height

        elif typ == "blank":
            y += text_line_height // 2

    svg += '</svg>'
    return svg


def main():
    """Generate both dark and light mode SVGs."""
    script_dir = Path(__file__).parent

    dark_svg = generate_svg("dark")
    dark_path = script_dir / "profile" / "dark_mode.svg"
    with open(dark_path, 'w', encoding='utf-8') as f:
        f.write(dark_svg)
    print(f"Generated: {dark_path}")

    light_svg = generate_svg("light")
    light_path = script_dir / "profile" / "light_mode.svg"
    with open(light_path, 'w', encoding='utf-8') as f:
        f.write(light_svg)
    print(f"Generated: {light_path}")


if __name__ == "__main__":
    main()
