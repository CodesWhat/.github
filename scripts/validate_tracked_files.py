import argparse
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys


def git_env():
    """Environment without git's repo-local variables (GIT_DIR, GIT_INDEX_FILE, ...)."""
    names = subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True, env=os.environ.copy(),
    ).split()
    return {key: value for key, value in os.environ.items() if key not in names}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("python", "data", "markdown"))
    kind = parser.parse_args().kind
    env = git_env()
    root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True, env=env,
    ).strip()
    os.chdir(root)
    # NUL delimiters preserve filenames containing spaces or newlines.
    paths = [
        Path(os.fsdecode(path))
        for path in subprocess.check_output(["git", "ls-files", "-z"], env=env).split(b"\0")
        if path
    ]

    if kind == "python":
        for path in paths:
            if path.suffix == ".py":
                py_compile.compile(str(path), doraise=True)
    elif kind == "data":
        for path in paths:
            if path.suffix == ".json":
                with path.open() as source:
                    json.load(source)
        yaml_paths = [str(path) for path in paths if path.suffix in (".yml", ".yaml")]
        if yaml_paths:
            subprocess.run(
                ["ruby", "-e", 'require "yaml"; ARGV.each { |path| YAML.parse_file(path) }',
                 "--", *yaml_paths], check=True,
            )
    else:
        profile = Path("profile/README.md")
        markdown = [path for path in paths if path.suffix == ".md" and path != profile]
        command = ["npx", "--yes", "markdownlint-cli2@0.23.2", "--no-globs"]
        if markdown:
            subprocess.run(
                [*command, *[":./" + str(path) for path in markdown]], check=True,
            )
        if profile in paths:
            subprocess.run(
                [*command, ":./" + str(profile),
                 "--config", ".github/markdownlint-profile.yaml"], check=True,
            )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
