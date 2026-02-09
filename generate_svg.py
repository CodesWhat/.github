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
            'art_solid': '#c9d1d9',   # bright white-gray for #
            'art_mid': '#6e7681',      # medium gray for + -
            'art_dim': '#30363d',      # subtle gray for .
            'title': '#58a6ff',        # bright blue
            'tagline': '#8b949e',      # muted gray
        }
    else:
        colors = {
            'bg': '#ffffff',
            'art_solid': '#24292f',    # near-black for #
            'art_mid': '#57606a',      # medium gray for + -
            'art_dim': '#d0d7de',      # light gray for .
            'title': '#0969da',        # blue
            'tagline': '#57606a',      # medium gray
        }

    c = colors
    width = 680
    font_size = 11
    art_line_height = 13
    text_line_height = 24
    y_start = 16

    logo_art = [
        "                              ##########################                              ",
        "                          ##################################                          ",
        "                      ##########################################                      ",
        "                   ############++-..................-++############                   ",
        "                ###########+-............................-+###########                ",
        "              #########+-....................................-+#########              ",
        "            #########-..........................................-#########            ",
        "          ########+................................................+########          ",
        "         #######+-...................................................+#######         ",
        "       ########-.....................................--++--...........-########       ",
        "      #######-...................................-###########+..........-#######      ",
        "     #######-.........-+###########-............+##############+.........-#######     ",
        "    #######.........-################+.........#######++++######+..........+######    ",
        "   #######.........-#######++++######+-.......-#####-.....-######-..........#######   ",
        "  ######+.........-######-.......+#-..........------......-######-...........#######  ",
        "  ######-.........######.................................-######-............-####### ",
        " ######-.........-#####+...............................-#######-..............-###### ",
        " ######..........-#####+.............................-#######-.................+######",
        "######-..........-######.............................######-...................-######",
        "######............######+.........-..................#####+.....................######",
        "#####+.............#######+-..--+###+-..........................................+#####",
        "#####+..............+#################-..............#####-.....................-#####",
        "#####-...............-+#############-................#####+.....................-#####",
        "#####-...................-+#####+-...................#####-.....................-#####",
        "#####-..........................................................................-#####",
        "#####+..........................................................................+#####",
        "######..............-+++++-............-+++++-............-++++++...............+#####",
        "######-.............-######-...........#######-...........######-...............######",
        "######+..............-######..........#########..........+#####+...............-######",
        " ######-..............+#####+........+#########+........-######................###### ",
        " ######+...............######-......-###########-......-######................-###### ",
        "  ######-..............-######......######-######......+#####-...............-######  ",
        "  #######...............-#####+....+#####-..######....-#####+................#######  ",
        "   #######-..............+#####-..-#####+...+#####+..-######-...............#######   ",
        "    #######-.............-######-.######.....+#####-.######-..............-#######    ",
        "     #######-.............-#####+######......-######+#####+..............-#######     ",
        "      #######+.............###########-.......-###########..............+#######      ",
        "        #######-............#########+.........+#########.............-########       ",
        "         ########-..........-#######+...........+#######-...........-########         ",
        "          #########-..............................-----...........-#########          ",
        "            #########+..........................................+#########            ",
        "              ##########-....................................-##########              ",
        "                ###########+--...........................-+###########                ",
        "                   #############++-................--+#############                   ",
        "                      ##########################################                      ",
        "                          ##################################                          ",
        "                               #########################                              ",
    ]

    # Build SVG with per-character coloring for depth
    lines = []
    for art in logo_art:
        lines.append(("art", art))
    lines.append(("blank", ""))
    lines.append(("blank", ""))
    lines.append(("title", "C o d e s W h a t ?"))
    lines.append(("blank", ""))
    lines.append(("tagline", "building things that make you go 'huh, neat'"))

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

    for typ, content in lines:
        if typ == "art" and content.strip():
            # Render art with per-character color spans
            # Group consecutive chars by color category
            line_width = len(content) * char_width
            start_x = (width - line_width) / 2

            # Build runs of same-colored characters
            runs = []
            i = 0
            while i < len(content):
                ch = content[i]
                if ch == '#':
                    color = c['art_solid']
                elif ch in '+-':
                    color = c['art_mid']
                elif ch == '.':
                    color = c['art_dim']
                else:
                    i += 1
                    continue

                # Collect run of same color
                start = i
                while i < len(content):
                    nch = content[i]
                    if nch == '#':
                        nc = c['art_solid']
                    elif nch in '+-':
                        nc = c['art_mid']
                    elif nch == '.':
                        nc = c['art_dim']
                    else:
                        break
                    if nc != color:
                        break
                    i += 1

                x = start_x + start * char_width
                text = escape_xml(content[start:i])
                runs.append(f'<text x="{x:.1f}" y="{y}" fill="{color}" font-size="{font_size}px">{text}</text>')

            svg += '\n'.join(runs) + '\n'
            y += art_line_height

        elif typ == "art":
            y += art_line_height

        elif typ == "title":
            svg += f'<text x="{width // 2}" y="{y}" text-anchor="middle" fill="{c["title"]}" font-size="20px" font-weight="bold" letter-spacing="0.15em">{escape_xml(content)}</text>\n'
            y += text_line_height

        elif typ == "tagline":
            svg += f'<text x="{width // 2}" y="{y}" text-anchor="middle" fill="{c["tagline"]}" font-size="13px">{escape_xml(content)}</text>\n'
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
