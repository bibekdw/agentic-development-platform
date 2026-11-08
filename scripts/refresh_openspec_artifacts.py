#!/usr/bin/env python3
"""Refresh configs/openspec-artifacts.json from the OpenSpec CLI pinned in configs/openspec.json.

Run this by hand after bumping the pin (needs Node >= the pinned minimum and network access):

    python3 scripts/refresh_openspec_artifacts.py

It creates a throwaway change in a temporary directory and records, for each artifact of the
configured schema, the CLI's own `instruction`, `template`, dependencies and output path. Project
specific `context` and `rules` are deliberately not stored: they come from each repository's
openspec/config.yaml at planning time. Review the resulting diff and commit it in a setup PR.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / "configs" / "openspec.json"
SNAPSHOT = ROOT / "configs" / "openspec-artifacts.json"
CHANGE = "artifact-snapshot"
KEEP = ("description", "instruction", "template", "outputPath", "dependencies")


def _cli(cfg, cwd, *args):
    npx = shutil.which("npx.cmd") or shutil.which("npx") or "npx"
    env = dict(os.environ, CI="true", OPENSPEC_TELEMETRY="0", NO_COLOR="1")
    out = subprocess.run([npx, "--yes", f"{cfg['package']}@{cfg['version']}", *args], cwd=cwd, env=env,
                         capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise SystemExit(f"openspec {' '.join(args)} failed: {(out.stdout + out.stderr).strip()[:400]}")
    return out.stdout


def build_snapshot(cfg):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "openspec").mkdir()
        (Path(tmp) / "openspec" / "config.yaml").write_text(f"schema: {cfg['schema']}\n")
        _cli(cfg, tmp, "new", "change", CHANGE)
        status = json.loads(_cli(cfg, tmp, "status", "--change", CHANGE, "--json"))
        ids = [a["id"] for a in status.get("artifacts", [])]
        if not ids:
            raise SystemExit("could not determine artifact ids from `openspec status --json`")
        artifacts = {}
        for aid in ids:
            data = json.loads(_cli(cfg, tmp, "instructions", aid, "--change", CHANGE, "--json"))
            artifacts[aid] = {k: data[k] for k in KEEP if k in data}
            artifacts[aid]["dependencies"] = [d["id"] for d in data.get("dependencies", [])]
    return {"generated_with": f"{cfg['package']}@{cfg['version']}", "schema": cfg["schema"],
            "artifact_order": ids, "artifacts": artifacts}


def main():
    cfg = json.loads(PIN.read_text(encoding="utf-8"))
    snap = build_snapshot(cfg)
    SNAPSHOT.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {SNAPSHOT.relative_to(ROOT)} ({len(snap['artifacts'])} artifacts, {snap['generated_with']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
