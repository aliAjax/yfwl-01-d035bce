"""Behavior checks for the storage-site inverse ledger task."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ENTRY = os.environ.get("YEL001_ENTRY", "/app/energy_ledger.py")


def _unit(unit_id: str, seed: int) -> dict:
    profile = []
    for index in range(12):
        value = (((seed + 2) * (index + 5)) % 13 + 1) * 0.73
        if (seed + index) % 5 == 0:
            value = -value * (0.35 + 0.1 * (index % 3))
        profile.append(round(value, 3))
    return {"unit_id": unit_id, "profile": profile, "capacity": 11.0 + seed % 4}


def _case(station_count: int = 4, group_size: int = 3, run_id: str = "RUN-1") -> dict:
    unit_count = station_count * group_size
    units = [_unit(f"U-{seed:02d}", seed) for seed in range(unit_count)]
    stations = []
    for station_index in range(station_count):
        indexes = list(range(station_index * group_size, (station_index + 1) * group_size))
        charge_loss = 0.03 + station_index * 0.006
        discharge_loss = 0.02 + station_index * 0.004
        grid = []
        for index in range(12):
            charge = sum(max(units[unit]["profile"][index], 0.0) for unit in indexes)
            discharge = sum(min(units[unit]["profile"][index], 0.0) for unit in indexes)
            grid.append(round(charge + discharge - charge_loss * charge - discharge_loss * discharge, 6))
        stations.append({"station_id": f"S-{station_index:02d}", "grid_energy": grid})
    requests = [
        {"station_id": f"S-{station_index:02d}", "energy_mwh": 30.0}
        for station_index in range(station_count)
    ]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "case_id": "CASE-001",
        "units": units,
        "stations": stations,
        "dispatch_request": requests,
    }


def _run(case: dict, tmp_path: Path) -> tuple[subprocess.CompletedProcess, dict, Path]:
    input_path = tmp_path / "scenario.json"
    output_dir = tmp_path / "result"
    input_path.write_text(json.dumps(case), encoding="utf-8")
    output_dir.mkdir(exist_ok=True)
    proc = subprocess.run(
        [sys.executable, ENTRY, str(input_path), str(output_dir)],
        capture_output=True,
        text=True,
        timeout=45,
    )
    report_path = output_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    return proc, report, output_dir


def _run_raw(raw: str, tmp_path: Path) -> subprocess.CompletedProcess:
    input_path = tmp_path / "scenario.json"
    output_dir = tmp_path / "result"
    input_path.write_text(raw, encoding="utf-8")
    output_dir.mkdir()
    return subprocess.run(
        [sys.executable, ENTRY, str(input_path), str(output_dir)],
        capture_output=True,
        text=True,
        timeout=45,
    )


def test_output_contract_creates_three_artifacts(tmp_path):
    """题面：「值班交接会产生一份报告、一个追加账本和一份恢复记录。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""
    assert (output_dir / "ledger.jsonl").is_file()
    assert (output_dir / "checkpoint.json").is_file()
    assert report["summary"]["unit_count"] == 12


def test_success_output_channels_are_silent(tmp_path):
    """题面：「成功时命令保持静默，标准输出与标准错误都不写下任何字符。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_top_level_keys_reject_missing_field(tmp_path):
    """题面：「储能交接单把 schema_version,run_id,case_id,units,stations,dispatch_request 放在同一层。」"""
    case = _case()
    del case["case_id"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_unit_keys_reject_extra_field(tmp_path):
    """题面：「每条储能单元记录只写 unit_id,profile,capacity 三个槽位。」"""
    case = _case()
    case["units"][0]["note"] = "extra"
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_station_keys_reject_missing_field(tmp_path):
    """题面：「每个变电站条目写 station_id 与 grid_energy 两个槽位。」"""
    case = _case()
    del case["stations"][0]["grid_energy"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_request_keys_reject_extra_field(tmp_path):
    """题面：「每条调度请求写 station_id 与 energy_mwh 两个槽位。」"""
    case = _case()
    case["dispatch_request"][0]["note"] = "extra"
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_profile_length_accepts_twelve_values(tmp_path):
    """题面：「profile 与 grid_energy 各有 12 个数值。」"""
    case = _case()
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 0
    assert len(case["units"][0]["profile"]) == 12
    assert report["summary"]["station_count"] == 4


def test_profile_length_rejects_eleven_values(tmp_path):
    """题面：「profile 与 grid_energy 各有 12 个数值。」"""
    case = _case()
    case["units"][0]["profile"] = case["units"][0]["profile"][:-1]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_duplicate_unit_identifier_is_refused(tmp_path):
    """题面：「unit_id 与 station_id 不得重复。」"""
    case = _case()
    case["units"][1]["unit_id"] = case["units"][0]["unit_id"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_boolean_schema_version_is_refused(tmp_path):
    """题面：「所有数值必须有限，布尔值不能顶替整数位置。」"""
    case = _case()
    case["schema_version"] = True
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_maximum_station_count_is_supported(tmp_path):
    """题面：「交接单允许 6 个站点、18 个单元。」"""
    proc, report, output_dir = _run(_case(station_count=6), tmp_path)
    assert proc.returncode == 0
    assert report["summary"]["station_count"] == 6
    assert report["summary"]["unit_count"] == 18


def test_station_count_divisibility_is_enforced(tmp_path):
    """题面：「station 数量必须整除 unit 数量。」"""
    case = _case()
    case["units"] = case["units"][:-1]
    case["stations"] = case["stations"][:-1]
    case["dispatch_request"] = case["dispatch_request"][:-1]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_group_sizes_must_be_equal(tmp_path):
    """题面：「每个 station 的单元数必须相同。」"""
    case = _case()
    case["stations"] = case["stations"][:2]
    case["dispatch_request"] = case["dispatch_request"][:2]
    case["units"] = case["units"][:6]
    first_grid = [0.0] * 12
    second_grid = [0.0] * 12
    for index in range(12):
        first_grid[index] = round(
            sum(case["units"][unit]["profile"][index] for unit in range(2)), 6
        )
        second_grid[index] = round(
            sum(case["units"][unit]["profile"][index] for unit in range(2, 6)), 6
        )
    case["stations"][0]["grid_energy"] = first_grid
    case["stations"][1]["grid_energy"] = second_grid
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert not (output_dir / "report.json").exists()
    assert report == {}


def test_report_bytes_end_with_newline(tmp_path):
    """题面：「最终文本不使用空格分隔，UTF-8 编码，文件只以一个换行收尾。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    raw = (output_dir / "report.json").read_bytes()
    assert proc.returncode == 0
    assert raw.endswith(b"\n")
    assert b'": ' not in raw


def test_report_key_set_is_exact(tmp_path):
    """题面：「报告外层七个键依次是 schema_version,case_id,run_id,assignments,losses,dispatch,summary。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert set(report) == {
        "schema_version",
        "case_id",
        "run_id",
        "assignments",
        "losses",
        "dispatch",
        "summary",
    }


def test_assignment_item_key_set_is_exact(tmp_path):
    """题面：「归属行交给下游的字段是 unit_id 和 station_id。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert all(set(item) == {"unit_id", "station_id"} for item in report["assignments"])


def test_loss_item_key_set_is_exact(tmp_path):
    """题面：「损耗行交给下游的字段是 station_id,charge_loss,discharge_loss。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert all(set(item) == {"station_id", "charge_loss", "discharge_loss"} for item in report["losses"])


def test_dispatch_item_key_set_is_exact(tmp_path):
    """题面：「调度行交给执行端的字段是 station_id,unit_id,energy_mwh。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert all(set(item) == {"station_id", "unit_id", "energy_mwh"} for item in report["dispatch"])


def test_summary_key_set_is_exact(tmp_path):
    """题面：「报告收口区放 unit_count,station_count,total_loss_mwh。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert set(report["summary"]) == {"unit_count", "station_count", "total_loss_mwh"}


def test_assignment_explains_station_totals(tmp_path):
    """题面：「assignments 要解释观测总量，不能只按输入顺序复制。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    groups = {item["station_id"]: [] for item in report["losses"]}
    for item in report["assignments"]:
        groups[item["station_id"]].append(item["unit_id"])
    assert proc.returncode == 0
    assert groups["S-00"] == ["U-00", "U-01", "U-02"]
    assert groups["S-01"] == ["U-03", "U-04", "U-05"]


def test_loss_values_are_rounded_six_places(tmp_path):
    """题面：「losses 是每位小数后六位的非负数。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    assert proc.returncode == 0
    assert all(value >= 0 for item in report["losses"] for value in (item["charge_loss"], item["discharge_loss"]))
    assert all(round(value, 6) == value for item in report["losses"] for value in (item["charge_loss"], item["discharge_loss"]))


def test_dispatch_rows_are_sorted_by_unit_id(tmp_path):
    """题面：「dispatch 按 unit_id 升序排列。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    rows = [(item["station_id"], item["unit_id"]) for item in report["dispatch"]]
    assert proc.returncode == 0
    assert rows == sorted(rows)
    assert len(rows) == 12


def test_repeated_run_keeps_report_bytes(tmp_path):
    """题面：「对同一场景重放，report.json 的每个字节都保持一致。」"""
    proc_first, report_first, output_dir = _run(_case(), tmp_path)
    first_bytes = (output_dir / "report.json").read_bytes()
    proc_second, report_second, _ = _run(_case(), tmp_path)
    assert proc_first.returncode == 0
    assert proc_second.returncode == 0
    assert (output_dir / "report.json").read_bytes() == first_bytes
    assert report_first == report_second


def test_repeated_run_does_not_append_ledger(tmp_path):
    """题面：「重放同一场景不会改变账本条目数量。」"""
    proc_first, report_first, output_dir = _run(_case(), tmp_path)
    first_ledger = (output_dir / "ledger.jsonl").read_bytes()
    proc_second, report_second, _ = _run(_case(), tmp_path)
    assert proc_first.returncode == 0
    assert proc_second.returncode == 0
    assert (output_dir / "ledger.jsonl").read_bytes() == first_ledger
    assert report_first == report_second


def test_changed_digest_appends_ledger(tmp_path):
    """题面：「场景字节摘要改变后，ledger.jsonl 追加一条新记录。」"""
    proc_first, report_first, output_dir = _run(_case(), tmp_path)
    changed = _case()
    changed["case_id"] = "CASE-002"
    proc_second, report_second, _ = _run(changed, tmp_path)
    lines = (output_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert proc_first.returncode == 0
    assert proc_second.returncode == 0
    assert len(lines) == 2
    assert report_first["case_id"] == "CASE-001"
    assert report_second["case_id"] == "CASE-002"


def test_ledger_line_has_exact_keys(tmp_path):
    """题面：「追加账本单行由 run_id/input_digest/status/unit_count/station_count 五段拼成。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    row = json.loads((output_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert proc.returncode == 0
    assert set(row) == {"run_id", "input_digest", "status", "unit_count", "station_count"}
    assert len(row["input_digest"]) == 64


def test_checkpoint_has_exact_keys(tmp_path):
    """题面：「恢复文件从 station_count 开始，后三个成员是 run_id,input_digest,report_digest。」"""
    proc, report, output_dir = _run(_case(), tmp_path)
    checkpoint = json.loads((output_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert proc.returncode == 0
    assert set(checkpoint) == {"run_id", "input_digest", "report_digest", "station_count"}
    assert len(checkpoint["report_digest"]) == 64


def test_missing_key_is_refused(tmp_path):
    """题面：「场景缺字段、带未知字段或出现重复 JSON 键时，入口返回拒收。」"""
    case = _case()
    del case["stations"]
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert "input refused:" in proc.stderr
    assert not (output_dir / "report.json").exists()
    assert not (output_dir / "report.json").exists()


def test_duplicate_json_key_is_refused(tmp_path):
    """题面：「场景缺字段、带未知字段或出现重复 JSON 键时，入口返回拒收。」"""
    raw = json.dumps(_case())[:-1] + ',"run_id":"RUN-2"}'
    proc = _run_raw(raw, tmp_path)
    assert proc.returncode == 2
    assert "input refused:" in proc.stderr
    assert not (tmp_path / "result" / "report.json").exists()


def test_refusal_uses_fixed_stderr_prefix(tmp_path):
    """题面：「标准错误承载拒收原因，input refused: 是固定的起始片段。」"""
    case = _case()
    case["station_count"] = 99
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert proc.stderr.splitlines()[0].startswith("input refused:")
    assert report == {}


def test_refusal_preserves_existing_artifacts(tmp_path):
    """题面：「拒收时既有 report.json,ledger.jsonl 和 checkpoint.json 保持原字节。」"""
    proc_first, report_first, output_dir = _run(_case(), tmp_path)
    old_report = (output_dir / "report.json").read_bytes()
    old_ledger = (output_dir / "ledger.jsonl").read_bytes()
    old_checkpoint = (output_dir / "checkpoint.json").read_bytes()
    broken = _case()
    del broken["dispatch_request"]
    proc_second, report_second, _ = _run(broken, tmp_path)
    assert proc_first.returncode == 0
    assert proc_second.returncode == 2
    assert (output_dir / "report.json").read_bytes() == old_report
    assert (output_dir / "ledger.jsonl").read_bytes() == old_ledger
    assert (output_dir / "checkpoint.json").read_bytes() == old_checkpoint


def test_dispatch_over_capacity_is_refused(tmp_path):
    """题面：「dispatch_request 的总电量需要落在可分配容量以内。」"""
    case = _case()
    case["dispatch_request"][0]["energy_mwh"] = 999.0
    proc, report, output_dir = _run(case, tmp_path)
    assert proc.returncode == 2
    assert "input refused:" in proc.stderr
    assert not (output_dir / "report.json").exists()
