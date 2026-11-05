#!/usr/bin/env python3
"""Offline safety/structure validation for cross-repository change manifests."""
import argparse
import json
import re
from pathlib import Path

DEFAULT_ALLOWED={"adlc-sample-backend", "adlc-sample-worker", "adlc-sample-ui", "adlc-sample-deploy"}
SHA40=re.compile(r"[a-fA-F0-9]{40}\Z")
CHANGE=re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")


def validate(manifest, allowed=None):
    allowed = DEFAULT_ALLOWED if allowed is None else set(allowed)
    errors=[]
    if not isinstance(manifest,dict):
        return ["manifest must be an object"]
    if set(manifest)!={"change_id","repositories","required_checks","production_approval_required"}:
        errors.append("unexpected/missing top-level fields")
    if not isinstance(manifest.get("change_id"),str) or not CHANGE.fullmatch(manifest["change_id"]):
        errors.append("invalid change_id")
    repos=manifest.get("repositories")
    if not isinstance(repos,list) or not 1<=len(repos)<=4:
        errors.append("repositories must contain 1–4 entries")
    else:
        names=set()
        for index,repo in enumerate(repos):
            if not isinstance(repo,dict) or not {"name","revision"}<=set(repo) or set(repo)-{"name","revision","pr_number"}:
                errors.append(f"repositories[{index}] invalid structure")
                continue
            name=repo['name']; rev=repo['revision']
            if not isinstance(name,str) or name not in allowed:
                errors.append(f"repositories[{index}] not allowlisted")
            if name in names:
                errors.append(f"repositories[{index}] duplicated repository")
            names.add(name)
            if not isinstance(rev,str) or not SHA40.fullmatch(rev):
                errors.append(f"repositories[{index}] must specify full commit SHA")
            if 'pr_number' in repo and (type(repo['pr_number']) is not int or repo['pr_number']<1):
                errors.append(f"repositories[{index}] invalid PR number")
    checks=manifest.get('required_checks')
    if not isinstance(checks,list) or not checks or any(not isinstance(s,str) or not s.strip() for s in checks):
        errors.append('required_checks must be nonempty list of nonempty strings')
    if manifest.get('production_approval_required') is not True:
        errors.append('production_approval_required must be true')
    return errors


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('manifest',type=Path)
    args=parser.parse_args()
    errors=validate(json.loads(args.manifest.read_text()))
    if errors:
        for msg in errors: print('ERROR:',msg)
        raise SystemExit(1)
    print('PASS: manifest structure valid (does not verify Git commit existence)')

if __name__=='__main__': main()
