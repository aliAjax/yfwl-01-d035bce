#!/usr/bin/env python3
"""Reference solver for the storage-site inverse ledger task.

The solver treats station totals as a signed energy observation.  A station
group is scored by fitting two loss coefficients in a small linear system;
the remaining exact-cover search selects one group per station.  The dispatch
request is then allocated over the recovered units in a deterministic order.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


TOP_KEYS = {"schema_version", "run_id", "case_id", "units", "stations", "dispatch_request"}
UNIT_KEYS = {"unit_id", "profile", "capacity"}
STATION_KEYS = {"station_id", "grid_energy"}
REQUEST_KEYS = {"station_id", "energy_mwh"}
REPORT_KEYS = {
    "schema_version",
    "case_id",
    "run_id",
    "assignments",
    "losses",
    "dispatch",
    "summary",
}
LEDGER_KEYS = {"run_id", "input_digest", "status", "unit_count", "station_count"}
CHECKPOINT_KEYS = {"run_id", "input_digest", "report_digest", "station_count"}


def _number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw, object_pairs_hook=_pairs_no_duplicates)
    if not isinstance(payload, dict):
        raise ValueError("top-level value must be an object")
    return payload


def _require_keys(obj: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError(f"{label} must be an object")
    if set(obj) != keys:
        raise ValueError(f"{label} key set does not match")
    return obj


def _validate(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    _require_keys(payload, TOP_KEYS, "input")
    if payload["schema_version"] != 1 or isinstance(payload["schema_version"], bool):
        raise ValueError("schema_version must be integer 1")
    if not isinstance(payload["run_id"], str) or not payload["run_id"]:
        raise ValueError("run_id must be a non-empty string")
    if not isinstance(payload["case_id"], str) or not payload["case_id"]:
        raise ValueError("case_id must be a non-empty string")
    units = payload["units"]
    stations = payload["stations"]
    requests = payload["dispatch_request"]
    if not isinstance(units, list) or not units:
        raise ValueError("units must be a non-empty array")
    if not isinstance(stations, list) or not stations:
        raise ValueError("stations must be a non-empty array")
    if not isinstance(requests, list):
        raise ValueError("dispatch_request must be an array")
    if len(units) > 18 or len(stations) > 6:
        raise ValueError("input exceeds the supported size")
    if len(units) % len(stations):
        raise ValueError("station count must divide unit count")
    if len(requests) != len(stations):
        raise ValueError("one dispatch request is required per station")

    unit_ids: set[str] = set()
    for item in units:
        _require_keys(item, UNIT_KEYS, "unit")
        unit_id = item["unit_id"]
        if not isinstance(unit_id, str) or not unit_id or unit_id in unit_ids:
            raise ValueError("unit ids must be unique non-empty strings")
        unit_ids.add(unit_id)
        profile = item["profile"]
        if not isinstance(profile, list) or len(profile) != 12:
            raise ValueError("profile must contain 12 values")
        if not all(_number(value) for value in profile):
            raise ValueError("profile contains a non-finite number")
        capacity = item["capacity"]
        if not _number(capacity) or float(capacity) <= 0:
            raise ValueError("capacity must be a positive finite number")

    station_ids: set[str] = set()
    for item in stations:
        _require_keys(item, STATION_KEYS, "station")
        station_id = item["station_id"]
        if not isinstance(station_id, str) or not station_id or station_id in station_ids:
            raise ValueError("station ids must be unique non-empty strings")
        station_ids.add(station_id)
        energy = item["grid_energy"]
        if not isinstance(energy, list) or len(energy) != 12:
            raise ValueError("grid_energy must contain 12 values")
        if not all(_number(value) for value in energy):
            raise ValueError("grid_energy contains a non-finite number")

    request_ids: set[str] = set()
    for item in requests:
        _require_keys(item, REQUEST_KEYS, "dispatch_request")
        station_id = item["station_id"]
        if not isinstance(station_id, str) or station_id not in station_ids:
            raise ValueError("dispatch request references an unknown station")
        if station_id in request_ids:
            raise ValueError("duplicate dispatch request")
        request_ids.add(station_id)
        energy = item["energy_mwh"]
        if not _number(energy) or float(energy) < 0:
            raise ValueError("dispatch energy must be a non-negative number")
    if request_ids != station_ids:
        raise ValueError("dispatch requests must cover every station")
    return units, stations, requests


def _fit_group(
    group: tuple[dict[str, Any], ...],
    grid: list[float],
    profile_len: int,
) -> tuple[float, float, float] | None:
    charge = [0.0] * profile_len
    discharge = [0.0] * profile_len
    for unit in group:
        for index, value in enumerate(unit["profile"]):
            numeric = float(value)
            if numeric >= 0:
                charge[index] += numeric
            else:
                discharge[index] += numeric
    base = [charge[i] + discharge[i] for i in range(profile_len)]
    residual_vector = [float(grid[i]) - base[i] for i in range(profile_len)]
    aa = sum(value * value for value in charge)
    bb = sum(value * value for value in discharge)
    ab = sum(charge[i] * discharge[i] for i in range(profile_len))
    ay = sum(charge[i] * residual_vector[i] for i in range(profile_len))
    by = sum(discharge[i] * residual_vector[i] for i in range(profile_len))
    det = aa * bb - ab * ab
    if abs(det) < 1e-12:
        return None
    charge_loss = (-ay * bb + by * ab) / det
    discharge_loss = (-by * aa + ay * ab) / det
    if charge_loss < 0 or discharge_loss < 0 or charge_loss > 0.25 or discharge_loss > 0.25:
        return None
    error = 0.0
    for index in range(profile_len):
        predicted = base[index] - charge_loss * charge[index] - discharge_loss * discharge[index]
        error += (predicted - float(grid[index])) ** 2
    return math.sqrt(error / profile_len), charge_loss, discharge_loss


def _candidate_groups(
    units: list[dict[str, Any]],
    station: dict[str, Any],
    group_size: int,
) -> list[tuple[float, tuple[int, ...], float, float]]:
    result: list[tuple[float, tuple[int, ...], float, float]] = []
    for indexes in itertools.combinations(range(len(units)), group_size):
        group = tuple(units[index] for index in indexes)
        fitted = _fit_group(group, station["grid_energy"], len(station["grid_energy"]))
        if fitted is None:
            continue
        residual, charge_loss, discharge_loss = fitted
        if residual <= 1e-5:
            result.append((residual, indexes, charge_loss, discharge_loss))
    result.sort(key=lambda item: (item[0], item[1]))
    return result


def _solve_assignment(
    units: list[dict[str, Any]],
    stations: list[dict[str, Any]],
) -> dict[int, tuple[tuple[int, ...], float, float, float]]:
    group_size = len(units) // len(stations)
    candidates = {
        index: _candidate_groups(units, station, group_size)
        for index, station in enumerate(stations)
    }
    if any(not value for value in candidates.values()):
        raise ValueError("no loss-consistent station grouping exists")
    order = sorted(range(len(stations)), key=lambda index: (len(candidates[index]), index))
    selected: dict[int, tuple[tuple[int, ...], float, float, float]] = {}
    best: tuple[float, tuple[tuple[int, ...], ...]] | None = None

    def search(position: int, used: int, total: float, groups: list[tuple[int, ...]]) -> None:
        nonlocal best
        if best is not None and total >= best[0] - 1e-12:
            return
        if position == len(order):
            signature = tuple(tuple(sorted(group)) for group in groups)
            if best is None or (total, signature) < (best[0], best[1]):
                best = (total, signature)
                selected.clear()
                for station_index, choice in zip(order, groups):
                    fitting = next(
                        item for item in candidates[station_index] if item[1] == choice
                    )
                    selected[station_index] = (choice, fitting[0], fitting[2], fitting[3])
            return
        station_index = order[position]
        for residual, indexes, charge_loss, discharge_loss in candidates[station_index]:
            mask = sum(1 << index for index in indexes)
            if used & mask:
                continue
            search(
                position + 1,
                used | mask,
                total + residual,
                groups + [indexes],
            )

    search(0, 0, 0.0, [])
    if best is None:
        raise ValueError("exact cover assignment not found")
    return selected


def _allocate_dispatch(
    units: list[dict[str, Any]],
    stations: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    assignment: dict[int, tuple[tuple[int, ...], float, float, float]],
) -> list[dict[str, Any]]:
    by_station = {item["station_id"]: item for item in requests}
    rows: list[dict[str, Any]] = []
    for station_index, station in enumerate(stations):
        indexes, _, _, _ = assignment[station_index]
        request = float(by_station[station["station_id"]]["energy_mwh"])
        remaining = request
        for index in sorted(indexes, key=lambda i: units[i]["unit_id"]):
            unit = units[index]
            amount = min(float(unit["capacity"]), remaining)
            if amount > 0:
                rows.append(
                    {
                        "station_id": station["station_id"],
                        "unit_id": unit["unit_id"],
                        "energy_mwh": round(amount, 6),
                    }
                )
            remaining = max(0.0, remaining - amount)
        if remaining > 1e-9:
            raise ValueError("dispatch request exceeds station capacity")
    rows.sort(key=lambda item: (item["station_id"], item["unit_id"]))
    return rows


def _canonical(report: dict[str, Any]) -> bytes:
    return (json.dumps(report, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _append_ledger(path: Path, row: dict[str, Any]) -> None:
    line = json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _build_report(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    units, stations, requests = _validate(payload)
    assignment = _solve_assignment(units, stations)
    assignments: list[dict[str, str]] = []
    losses: list[dict[str, Any]] = []
    for index, station in enumerate(stations):
        indexes, residual, charge_loss, discharge_loss = assignment[index]
        for unit_index in indexes:
            assignments.append({"unit_id": units[unit_index]["unit_id"], "station_id": station["station_id"]})
        losses.append(
            {
                "station_id": station["station_id"],
                "charge_loss": round(charge_loss, 6),
                "discharge_loss": round(discharge_loss, 6),
            }
        )
    assignments.sort(key=lambda item: (item["station_id"], item["unit_id"]))
    losses.sort(key=lambda item: item["station_id"])
    dispatch = _allocate_dispatch(units, stations, requests, assignment)
    report = {
        "schema_version": 1,
        "case_id": payload["case_id"],
        "run_id": payload["run_id"],
        "assignments": assignments,
        "losses": losses,
        "dispatch": dispatch,
        "summary": {
            "unit_count": len(units),
            "station_count": len(stations),
            "total_loss_mwh": round(
                sum(
                    sum(float(request["energy_mwh"]) for request in requests)
                    * (item["charge_loss"] + item["discharge_loss"])
                    / 2
                    for item in losses
                ),
                6,
            ),
        },
    }
    if set(report) != REPORT_KEYS:
        raise ValueError("internal report key set error")
    return report, hashlib.sha256(_canonical(report)).hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("input refused: expected input and output paths", file=sys.stderr)
        return 2
    input_path = Path(argv[1])
    output_dir = Path(argv[2])
    report_path = output_dir / "report.json"
    ledger_path = output_dir / "ledger.jsonl"
    checkpoint_path = output_dir / "checkpoint.json"
    try:
        payload = _read_json(input_path)
        input_digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
        report, report_digest = _build_report(payload)
        output_dir.mkdir(parents=True, exist_ok=True)
        ledger_row = {
            "run_id": payload["run_id"],
            "input_digest": input_digest,
            "status": "complete",
            "unit_count": len(payload["units"]),
            "station_count": len(payload["stations"]),
        }
        _require_keys(ledger_row, LEDGER_KEYS, "ledger")
        checkpoint = {
            "run_id": payload["run_id"],
            "input_digest": input_digest,
            "report_digest": report_digest,
            "station_count": len(payload["stations"]),
        }
        _require_keys(checkpoint, CHECKPOINT_KEYS, "checkpoint")
        previous: list[str] = []
        if ledger_path.exists():
            previous = ledger_path.read_text(encoding="utf-8").splitlines()
        row_text = json.dumps(ledger_row, ensure_ascii=True, separators=(",", ":"))
        if not previous or previous[-1] != row_text:
            _append_ledger(ledger_path, ledger_row)
        _atomic_write(report_path, _canonical(report))
        _atomic_write(
            checkpoint_path,
            (json.dumps(checkpoint, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"input refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
