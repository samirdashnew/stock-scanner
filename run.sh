#!/usr/bin/env bash
# Starts the local dashboard server at http://localhost:8000
#
# This does NOT run a scan itself. Run one of these first (or anytime you
# want fresh data, then just refresh the browser tab):
#   ./venv/bin/python morning_scan.py   -> morning.html  (9:30-10:00am shortlist)
#   ./venv/bin/python scanner.py        -> index.html    (broader EOD scan)
set -e
cd "$(dirname "$0")"

echo "Starting dashboard at http://localhost:8000"
echo "  Morning Picks:  http://localhost:8000/morning.html"
echo "  Full scanner:   http://localhost:8000/index.html"
echo "(Press Ctrl+C to stop)"
./venv/bin/python -m http.server 8000
