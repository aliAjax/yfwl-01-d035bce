#!/usr/bin/env python3
"""Small legacy reader for the storage handover export.

The prior shift only kept the schema checks and a placeholder summary.  The
inverse assignment and loss fitting still need to be completed in this entry
point.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def read_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate)
    if not isinstance(payload, dict):
        raise ValueError("top-level object required")
    return payload


def write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=True, separators=(",", ":")) + "\n"
    (output_dir / "report.json").write_text(text, encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("input refused: expected input and output paths", file=sys.stderr)
        return 2
    input_path = Path(argv[1])
    output_dir = Path(argv[2])
    try:
        payload = read_payload(input_path)
        top = set(payload)
        expected = {
            "schema_version",
            "run_id",
            "case_id",
            "units",
            "stations",
            "dispatch_request",
        }
        if top != expected:
            raise ValueError("top-level keys do not match")
        units = payload["units"]
        stations = payload["stations"]
        if not isinstance(units, list) or not isinstance(stations, list):
            raise ValueError("units and stations must be arrays")
        for unit in units:
            if not isinstance(unit, dict) or set(unit) != {"unit_id", "profile", "capacity"}:
                raise ValueError("unit shape does not match")
            if not isinstance(unit["unit_id"], str) or not unit["unit_id"]:
                raise ValueError("unit id is empty")
            if not isinstance(unit["profile"], list) or len(unit["profile"]) != 12:
                raise ValueError("unit profile length does not match")
            if not all(finite_number(value) for value in unit["profile"]):
                raise ValueError("unit profile contains a non-number")
            if not finite_number(unit["capacity"]) or float(unit["capacity"]) <= 0:
                raise ValueError("unit capacity is not positive")
        for station in stations:
            if not isinstance(station, dict) or set(station) != {"station_id", "grid_energy"}:
                raise ValueError("station shape does not match")
            if not isinstance(station["station_id"], str) or not station["station_id"]:
                raise ValueError("station id is empty")
            if not isinstance(station["grid_energy"], list) or len(station["grid_energy"]) != 12:
                raise ValueError("station energy length does not match")
            if not all(finite_number(value) for value in station["grid_energy"]):
                raise ValueError("station energy contains a non-number")
        if len(units) > 18 or len(stations) > 6 or not stations or not units:
            raise ValueError("input size is outside the supported range")
        if len(units) % len(stations):
            raise ValueError("unit groups are not even")
        report = {
            "schema_version": 1,
            "case_id": payload["case_id"],
            "run_id": payload["run_id"],
            "assignments": [],
            "losses": [],
            "dispatch": [],
            "summary": {
                "unit_count": len(units),
                "station_count": len(stations),
                "total_loss_mwh": 0.0,
            },
        }
        write_report(output_dir, report)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"input refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
