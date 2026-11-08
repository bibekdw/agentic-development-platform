#!/usr/bin/env python3
"""Print GitHub Actions outputs describing the repositories the ADLC GitHub App token must cover.

The list comes from configs/repositories.json (the single allowlist) plus the platform repo,
so workflows never carry their own copy of repository names.

Usage: app_scope.py <platform-repo-name>   -> appends to $GITHUB_OUTPUT (or prints)
"""
import json
import os
import sys
from pathlib import Path


def scope(cfg, platform_name):
    return [platform_name, *cfg["allowed_repositories"]]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or "/" in argv[0]:
        raise SystemExit("usage: app_scope.py <platform-repo-name (no owner)>")
    cfg = json.loads((Path(__file__).resolve().parents[1] / "configs" / "repositories.json").read_text())
    line = f"repositories={','.join(scope(cfg, argv[0]))}\nowner={cfg['owner']}\n"
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line)
    else:
        print(line, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
