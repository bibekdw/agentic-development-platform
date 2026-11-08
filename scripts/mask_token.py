#!/usr/bin/env python3
"""Tell the Actions runner to mask the Basic-auth form of $GH_TOKEN (the raw token is masked automatically).

Run as its own command, never inside a `| tee file` pipeline: the runner command must not end up in a log
file or artifact (it contains the credential).
"""
import base64
import os
import sys

token = os.environ.get("GH_TOKEN", "")
if token:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    sys.stdout.write(f"::add-mask::{basic}\n")
    sys.stdout.flush()
