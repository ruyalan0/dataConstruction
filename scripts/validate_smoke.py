"""Validate persisted results, independently of Isaac's shutdown exit status."""

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
args = parser.parse_args()
if (args.output / "error.txt").exists():
    raise RuntimeError((args.output / "error.txt").read_text())
status = json.loads((args.output / "exit-status.json").read_text())
result = json.loads((args.output / "result.json").read_text())
if status["exit_code"] != 0 or result["status"] != "completed":
    raise RuntimeError("Simulator did not complete")
hold, lift = result["feedback"]
if hold["interrupted"] or lift["interrupted"]:
    raise RuntimeError("Tracking interrupted")
if max(hold["error_mm"].values()) > 2 or max(hold["error_deg"].values()) > 2:
    raise RuntimeError("Hold calibration failed")
if not 2 <= lift["displacement_mm"]["left"][2] <= 8:
    raise RuntimeError("5 mm lift calibration failed")
if lift["error_mm"]["right"] > 2 or lift["error_deg"]["right"] > 2:
    raise RuntimeError("Inactive arm drift")
print(json.dumps({"control_calibration_passed": True, "official_score": None}))
