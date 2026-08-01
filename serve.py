#!/usr/bin/env python3
"""Dev server launcher.

Runs under the system python3 with the local venv's site-packages prepended, so
it works in sandboxed environments that won't execute the venv's own symlinked
interpreter. Equivalent to:

    .venv/bin/uvicorn app.main:app --port 8123
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, ".venv", "lib",
                    "python%d.%d" % sys.version_info[:2], "site-packages")
if os.path.isdir(SITE):
    sys.path.insert(0, SITE)
sys.path.insert(0, HERE)
os.chdir(HERE)

import uvicorn  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8123"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")
