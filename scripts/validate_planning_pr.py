#!/usr/bin/env python3
"""Deterministic validation of one repository's planning change.

Used by (a) the installed planning-pr-validate workflow in each participating repo and
(b) the publisher before it opens a PR, so both apply identical checks:
  1. change id derived from the branch is well formed and matches the folder
  2. only the change folder changed relative to the base (allowed-path check)
  3. adlc-plan.json metadata and OpenSpec file structure / traceability
  4. `openspec validate` with the version pinned in configs/openspec.json
Exit status is non-zero when any check fails.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import planning as p  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "openspec.json"


def load_openspec_config(path=DEFAULT_CONFIG):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def openspec_command(cfg, change_id):
    npx = shutil.which("npx.cmd") or shutil.which("npx") or "npx"
    args = [a.replace("{change_id}", change_id) for a in cfg["validate_args"]]
    return [npx, "--yes", f"{cfg['package']}@{cfg['version']}", *args]


def run_openspec(root, change_id, cfg=None):
    """Run the pinned OpenSpec validator. Returns (ok, output). Never interactive."""
    cfg = cfg or load_openspec_config()
    base = Path(root) / "openspec" / "changes" / change_id
    if not base.is_dir():  # OpenSpec reports a missing change as 'no deltas'; be explicit
        return False, f"change folder not found: openspec/changes/{change_id}"
    env = dict(os.environ, CI="true", OPENSPEC_TELEMETRY="0", NO_COLOR="1")
    try:
        out = subprocess.run(openspec_command(cfg, change_id), cwd=root, env=env, capture_output=True,
                             text=True, timeout=180, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"openspec could not run: {exc}"
    return out.returncode == 0, (out.stdout + out.stderr).strip()


def changed_paths(root, base_ref):
    out = subprocess.run(["git", "diff", "--name-only", f"{base_ref}...HEAD"], cwd=root,
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git diff failed: {out.stderr.strip()[:200]}")
    return [line for line in out.stdout.splitlines() if line]


def validate_repo(root, change_id, base_ref, repository, run_cli=True, cfg=None):
    """Return (errors, meta). `repository` is owner/repo of the repo under validation."""
    errors = []
    if not p.CHANGE_ID.fullmatch(change_id or ""):
        return [f"invalid change id {change_id!r}"], None
    if not (Path(root) / "openspec" / "config.yaml").is_file():
        errors.append("OpenSpec is not configured in this repository (openspec/config.yaml missing)")
    errors += p.check_changed_paths(change_id, changed_paths(root, base_ref))
    merrs, meta = p.validate_plan_metadata(root, change_id, repository)
    errors += merrs
    if meta:
        errors += p.validate_change_folder(root, change_id, meta["story"], meta["contract_refs"], meta["epic"])
    if run_cli and not errors:
        ok, output = run_openspec(root, change_id, cfg)
        if not ok:
            errors.append("openspec validate failed:\n" + output)
    return errors, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("."))
    ap.add_argument("--branch", required=True, help="head branch, adlc/plan/<change-id>")
    ap.add_argument("--base-ref", required=True, help="e.g. origin/main")
    ap.add_argument("--repository", required=True, help="owner/repo being validated")
    ap.add_argument("--no-openspec", action="store_true")
    a = ap.parse_args(argv)
    prefix = "adlc/plan/"
    if not a.branch.startswith(prefix):
        print("ERROR: not a planning branch")
        return 1
    errors, _ = validate_repo(a.root, a.branch[len(prefix):], a.base_ref, a.repository, not a.no_openspec)
    for m in errors:
        print("ERROR:", m)
    if not errors:
        print("PASS: planning change valid")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
