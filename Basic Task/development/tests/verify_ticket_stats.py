#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C01/C02 模型输出验证脚本。

用途
----
按 `编程输入03-ticket_stats.py` 文件开头「功能约定」与文件内 `EDGE_SCENARIOS`
列表，对"模型修复后的版本"逐项判定通过/失败，并与原始待审查版本对比。

用法
----
    python3 verify_ticket_stats.py --fixed <模型修复版.py> [--orig <原始版.py>] \
                                   [--out <结果.json>]

默认原始版路径为仓库内的
    ../../提示词与输入文档/输入文档/编程输入03-ticket_stats.py
（相对于本脚本所在目录，可用 --orig 覆盖）。

设计说明
--------
* 只做只读导入，不修改任何被检文件（临时副本写入系统临时目录）。
* 每个用例以"期望结果"为准做断言，异常与错误返回值都判为失败。
* 输出 JSON 供 development/results/<运行编号>/ 存档。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any

# --------------------------------------------------------------------------
# 用例定义：每条 = (用例名, 输入, 校验函数)
# 校验函数返回 (是否通过, 说明)。异常一律判失败。
# --------------------------------------------------------------------------

CST = timezone(timedelta(hours=8))
UTC = timezone.utc


CASES: list[tuple[str, list[dict], Any]] = []


def _rec(id: str = "", owner: Any = "", status: Any = "", **kw: Any) -> dict:
    """构造一条工单记录；传 None 表示该键缺失。"""
    rec: dict = {}
    if id is not None:
        rec["id"] = id
    if owner is not None:
        rec["owner"] = owner
    if status is not None:
        rec["status"] = status
    rec.update(kw)
    return rec


def _ok(cond: bool, msg: str) -> tuple[bool, str]:
    return bool(cond), msg


def add_case(name: str, records: list[dict], checker) -> None:
    CASES.append((name, records, checker))


# 1. 空输入 ---------------------------------------------------------------
add_case(
    "空输入：total=0、四状态=0、by_owner={}、average_closed_hours=None",
    [],
    lambda r: _ok(
        r["total"] == 0
        and all(r["by_status"][s] == 0 for s in ("open", "processing", "closed", "other"))
        and r["by_owner"] == {}
        and r["average_closed_hours"] is None,
        f"实际={r}",
    ),
)

# 2. 缺 owner 键（约定：归 unassigned，记录仍计数）--------------------------
add_case(
    "缺 owner 键：归 unassigned，不抛异常且计入统计",
    [_rec(id="T001", owner=None, status="open", created_at="2026-10-12T09:00:00+08:00")],
    lambda r: _ok(
        r["total"] == 1
        and r["by_status"]["open"] == 1
        and "unassigned" in r["by_owner"]
        and r["by_owner"]["unassigned"]["total"] == 1,
        f"实际={r}",
    ),
)

# 3. 缺 status 键（约定：归 other）----------------------------------------
add_case(
    "缺 status 键：归 other，不抛异常且计入统计",
    [_rec(id="T001", owner="Chen", status=None, created_at="2026-10-12T09:00:00+08:00")],
    lambda r: _ok(
        r["total"] == 1
        and r["by_status"]["other"] == 1
        and r["by_owner"].get("Chen", {}).get("total") == 1,
        f"实际={r}",
    ),
)

# 4. owner 为空串 / None / 纯空格（约定：均归 unassigned）-------------------
add_case(
    "owner 为空串/None/纯空格：全部归 unassigned",
    [
        _rec(id="T001", owner="", status="open", created_at="2026-10-12T09:00:00+08:00"),
        _rec(id="T002", owner=None, status="open", created_at="2026-10-12T09:00:00+08:00"),
        _rec(id="T003", owner="   ", status="open", created_at="2026-10-12T09:00:00+08:00"),
    ],
    lambda r: _ok(
        r["total"] == 3
        and r["by_owner"].get("unassigned", {}).get("total") == 3
        and r["by_status"]["open"] == 3,
        f"实际={r}",
    ),
)

# 5. status 大小写与首尾空格（约定：trim + lower 后归入四类之一）-----------
add_case(
    "status 大小写/首尾空格：归一化为 open/processing/closed",
    [
        _rec(id="T001", owner="Chen", status=" Open ", created_at="2026-10-12T09:00:00+08:00"),
        _rec(id="T002", owner="Chen", status="PROCESSING", created_at="2026-10-12T09:00:00+08:00"),
        _rec(id="T003", owner="Chen", status="Closed", created_at="2026-10-12T09:00:00+08:00"),
    ],
    lambda r: _ok(
        r["total"] == 3
        and r["by_status"]["open"] == 1
        and r["by_status"]["processing"] == 1
        and r["by_status"]["closed"] == 1
        and r["by_status"]["other"] == 0
        # by_owner 必须按“处理人”聚合，而非按“工单号”；
        # 否则同一处理人的多条记录会被拆成多个键，属结构性错误。
        and set(r["by_owner"].keys()) == {"Chen"}
        and r["by_owner"]["Chen"]["total"] == 3,
        f"实际={r}（期望 by_owner 仅有 Chen 一个键、total=3）",
    ),
)

# 6. status 未知值（约定：归 other）--------------------------------------
add_case(
    "status 未知值：归 other，不抛异常",
    [_rec(id="T001", owner="Chen", status="unknown", created_at="2026-10-12T09:00:00+08:00")],
    lambda r: _ok(r["total"] == 1 and r["by_status"]["other"] == 1, f"实际={r}"),
)

# 7. 重复 id（约定：只统计第一条）----------------------------------------
add_case(
    "重复 id：只统计第一条，total 为去重后数量",
    [
        _rec(id="T001", owner="Chen", status="open", created_at="2026-10-12T09:00:00+08:00"),
        _rec(id="T001", owner="Chen", status="closed", created_at="2026-10-12T09:00:00+08:00",
             closed_at="2026-10-12T10:00:00+08:00"),
    ],
    lambda r: _ok(
        r["total"] == 1
        and r["by_status"]["open"] == 1
        and r["by_status"]["closed"] == 0,
        f"实际={r}（期望 total=1、open=1、closed=0）",
    ),
)

# 8. id 缺失 / 纯空格（约定：跳过）----------------------------------------
add_case(
    "id 缺失或纯空格：跳过该记录，不抛异常",
    [
        _rec(id=None, owner="Chen", status="open", created_at="2026-10-12T09:00:00+08:00"),
        _rec(id="   ", owner="Chen", status="open", created_at="2026-10-12T09:00:00+08:00"),
        _rec(id="T001", owner="Chen", status="open", created_at="2026-10-12T09:00:00+08:00"),
    ],
    lambda r: _ok(r["total"] == 1 and r["by_status"]["open"] == 1, f"实际={r}"),
)

# 9. closed 工单缺 closed_at（约定：保留计数，跳过耗时）--------------------
add_case(
    "closed 工单缺 closed_at：保留计数，跳过耗时，不抛异常",
    [_rec(id="T001", owner="Chen", status="closed", created_at="2026-10-12T09:00:00+08:00")],
    lambda r: _ok(
        r["total"] == 1
        and r["by_status"]["closed"] == 1
        and r["average_closed_hours"] is None,
        f"实际={r}",
    ),
)

# 10. 时间格式错误（约定：保留计数，跳过耗时）------------------------------
add_case(
    "closed_at 格式错误：保留计数，跳过耗时，不抛异常",
    [
        _rec(id="T001", owner="Chen", status="closed", created_at="2026-10-12T09:00:00+08:00",
             closed_at="not-a-time")
    ],
    lambda r: _ok(
        r["total"] == 1 and r["by_status"]["closed"] == 1 and r["average_closed_hours"] is None,
        f"实际={r}",
    ),
)

# 11. 时间缺时区（约定：均含时区才计入耗时）-------------------------------
add_case(
    "时间缺少时区：跳过耗时统计，保留计数",
    [
        _rec(id="T001", owner="Chen", status="closed", created_at="2026-10-12T09:00:00",
             closed_at="2026-10-12T18:00:00")
    ],
    lambda r: _ok(
        r["total"] == 1 and r["by_status"]["closed"] == 1 and r["average_closed_hours"] is None,
        f"实际={r}",
    ),
)

# 12. 关闭早于创建（约定：跳过耗时）--------------------------------------
add_case(
    "关闭时间早于创建时间：跳过耗时统计，保留计数",
    [
        _rec(id="T001", owner="Chen", status="closed", created_at="2026-10-12T18:00:00+08:00",
             closed_at="2026-10-12T09:00:00+08:00")
    ],
    lambda r: _ok(
        r["total"] == 1 and r["by_status"]["closed"] == 1 and r["average_closed_hours"] is None,
        f"实际={r}",
    ),
)

# 13. 不同 UTC 偏移（约定：可解析且含时区即计入）-------------------------
add_case(
    "创建与关闭使用不同 UTC 偏移：计入耗时",
    [
        _rec(id="T001", owner="Chen", status="closed", created_at="2026-10-12T09:00:00+08:00",
             closed_at="2026-10-12T02:00:00+00:00")
    ],
    lambda r: _ok(
        r["total"] == 1
        and r["average_closed_hours"] is not None
        and abs(float(r["average_closed_hours"])) < 1e-9,
        f"实际={r['average_closed_hours']}（两者为同一时刻，耗时应为 0.0）",
    ),
)

# 14. 平均耗时保留两位小数 ----------------------------------------------
add_case(
    "平均耗时保留两位小数",
    [
        _rec(id="T001", owner="Chen", status="closed", created_at="2026-10-12T09:00:00+08:00",
             closed_at="2026-10-12T10:20:00+08:00"),
        _rec(id="T002", owner="Chen", status="closed", created_at="2026-10-12T09:00:00+08:00",
             closed_at="2026-10-12T11:00:00+08:00"),
    ],
    lambda r: _ok(
        r["average_closed_hours"] is not None
        and float(r["average_closed_hours"]) == 1.67,
        f"实际={r['average_closed_hours']}（期望 1.67，须保留两位小数）",
    ),
)


# --------------------------------------------------------------------------
# 载入与执行
# --------------------------------------------------------------------------

def load_module(path: str, name: str):
    """从指定路径载入模块（不污染 sys.path 顺序）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法载入模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run_suite(module) -> list[dict]:
    results = []
    for name, records, checker in CASES:
        entry: dict[str, Any] = {"case": name, "input": records}
        try:
            summary = module.summarize_tickets(records)
            entry["output"] = summary
            passed, detail = checker(summary)
            entry["passed"] = bool(passed)
            entry["detail"] = detail
        except Exception as exc:  # noqa: BLE001 - 任何异常都算失败
            entry["passed"] = False
            entry["output"] = None
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["traceback"] = traceback.format_exc(limit=3)
            entry["detail"] = "抛出异常，判为失败"
        results.append(entry)
    return results


def summarize(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    return {
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "failed_cases": [r["case"] for r in results if not r["passed"]],
    }


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_orig = os.path.normpath(
        os.path.join(here, "..", "..", "提示词与输入文档", "输入文档", "编程输入03-ticket_stats.py")
    )

    parser = argparse.ArgumentParser(description="验证 ticket_stats 修复版本是否符合功能约定")
    parser.add_argument("--fixed", required=True, help="模型修复后的版本路径")
    parser.add_argument("--orig", default=default_orig, help="原始待审查版本路径")
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径")
    parser.add_argument("--run-id", default=None, help="运行编号，写入结果文件")
    args = parser.parse_args()

    report: dict[str, Any] = {
        "run_id": args.run_id,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "orig_path": os.path.abspath(args.orig),
        "fixed_path": os.path.abspath(args.fixed),
    }

    tmpdir = None
    try:
        for label, src in (("orig", args.orig), ("fixed", args.fixed)):
            if not os.path.isfile(src):
                print(f"[错误] 找不到文件：{src}", file=sys.stderr)
                return 2
            # 直接按路径载入模块，不复制任何文件（避免临时目录权限问题，
            # 同时保证被检文件完全不被触碰）。
            try:
                mod = load_module(os.path.abspath(src), f"{label}_ticket_stats")
                report[label] = {"results": run_suite(mod), "source": os.path.abspath(src)}
                report[label]["summary"] = summarize(report[label]["results"])
            except Exception as exc:  # 整份文件都无法载入（语法错误等）
                report[label] = {
                    "source": os.path.abspath(src),
                    "load_error": f"{type(exc).__name__}: {exc}",
                    "summary": {"total_cases": len(CASES), "passed": 0,
                                "failed": len(CASES), "pass_rate": 0.0,
                                "failed_cases": ["文件无法载入"]},
                }
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # 控制台表格
    def line(label: str) -> None:
        data = report[label]
        s = data["summary"]
        print(f"\n=== {label} : {s['passed']}/{s['total_cases']} 通过 "
              f"(通过率 {s['pass_rate'] * 100:.1f}%) ===")
        if "load_error" in data:
            print(f"  [载入失败] {data['load_error']}")
            return
        for r in data["results"]:
            mark = "PASS" if r["passed"] else "FAIL"
            extra = r.get("error") or r.get("detail", "")
            print(f"  [{mark}] {r['case']}")
            if not r["passed"]:
                print(f"         -> {extra}")

    print("=" * 78)
    print("ticket_stats 修复版本验证（对照文件头部功能约定）")
    print("=" * 78)
    line("orig")
    line("fixed")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
        print(f"\n结果已写入：{os.path.abspath(args.out)}")

    return 0 if report["fixed"]["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
