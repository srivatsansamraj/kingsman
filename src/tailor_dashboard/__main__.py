"""python -m tailor_dashboard [--port 8770]: serve the dashboard at http://127.0.0.1:<port>/, on this machine only."""

from __future__ import annotations

import argparse

import uvicorn

from .server import create_app

parser = argparse.ArgumentParser(prog="tailor_dashboard", description="The job tracker, on this machine only.")
parser.add_argument("--port", type=int, default=8770)
arguments = parser.parse_args()
print(f"Kingsman dashboard: http://127.0.0.1:{arguments.port}/  (Ctrl+C stops it)")
# Open event streams would otherwise hold a stop until each page closes.
uvicorn.run(create_app(), host="127.0.0.1", port=arguments.port, log_level="warning", timeout_graceful_shutdown=2)
