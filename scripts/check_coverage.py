"""Enforce independent line and branch thresholds from coverage.py JSON output."""

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else "coverage.json").read_text())
totals = report["totals"]
metrics = {
    "lines": (totals["covered_lines"], totals["num_statements"]),
    "branches": (totals["covered_branches"], totals["num_branches"]),
}
failed = False
for name, (covered, total) in metrics.items():
    percentage = 100 * covered / total if total else 0
    print(f"{name}: {percentage:.2f}% ({covered}/{total}); required >=95%")
    failed = failed or percentage < 95
raise SystemExit(1 if failed else 0)
