#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""性能指标计算：从逐请求原始记录汇总出可写入 summary.json 的统计结果。

对应要求
--------
* `src/README.md`      —— 「性能测试」
* `tests/README.md`    —— 「性能指标计算」「请求失败后的计数」
* `任务介绍.md` 第 6 节 —— TTFT / TPOT / 吞吐量 / 完成耗时 / 成功率 的口径

口径约定（与报告必须一致）
--------------------------
TTFT     客户端提交 → 首个含非空内容的流式块，含排队与传输。
TPOT     (完成 − 首 token) ÷ (输出 token 数 − 1)，【排除首 token】。
         输出 token ≤ 1 时无法计算，记为 None。
完成耗时  完成 − 提交，含排队、提示词处理、生成与传输。
吞吐量    成功请求的输出 token 总数 ÷ 统计窗口（最早提交 → 最晚完成）。
成功率    成功请求数 ÷ 全部请求数。
百分位    最近秩法（nearest-rank）：位置 = ceil(k/100 × 样本数)。

状态四态（任务要求"成功、失败、超时、取消"）
--------------------------------------------
success / failed / timeout / cancelled
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from typing import Any, Iterable

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_CANCELLED = "cancelled"
ALL_STATUSES = (STATUS_SUCCESS, STATUS_FAILED, STATUS_TIMEOUT, STATUS_CANCELLED)


# ---------------------------------------------------------------------------
# 一、基础统计
# ---------------------------------------------------------------------------

def percentile(values: Iterable[float], k: float) -> float | None:
    """第 k 百分位数（最近秩法）。空样本返回 None，样本量为 1 时返回该值。"""
    data = sorted(float(v) for v in values if v is not None)
    if not data:
        return None
    if len(data) == 1 or k <= 0:
        return data[0]
    if k >= 100:
        return data[-1]
    rank = math.ceil(k / 100.0 * len(data))
    return data[min(max(rank - 1, 0), len(data) - 1)]


def median(values: Iterable[float]) -> float | None:
    """中位数（第 50 百分位）。"""
    return percentile(values, 50)


def mean(values: Iterable[float]) -> float | None:
    """算术平均值；空样本返回 None。"""
    data = [float(v) for v in values if v is not None]
    return (sum(data) / len(data)) if data else None


def _round(value: float | None, digits: int = 3) -> float | None:
    """统一小数位，避免汇总里出现超长浮点数。"""
    return None if value is None else round(float(value), digits)


# ---------------------------------------------------------------------------
# 二、逐请求派生指标
# ---------------------------------------------------------------------------

def compute_ttft_ms(record: dict) -> float | None:
    """TTFT（毫秒）。优先用记录里已算好的值，否则由时间戳推算。"""
    if record.get("ttft_ms") is not None:
        return float(record["ttft_ms"])
    submit, first = record.get("t_submit"), record.get("t_first_token")
    if submit is None or first is None:
        return None
    return (first - submit) * 1000.0


def compute_e2e_ms(record: dict) -> float | None:
    """完成耗时（毫秒）。"""
    if record.get("e2e_ms") is not None:
        return float(record["e2e_ms"])
    submit, done = record.get("t_submit"), record.get("t_complete")
    if submit is None or done is None:
        return None
    return (done - submit) * 1000.0


def compute_tpot_ms(record: dict) -> float | None:
    """TPOT（毫秒/token），排除首 token。

    (完成 − 首 token) ÷ (输出 token 数 − 1)
    """
    if record.get("tpot_ms") is not None:
        return float(record["tpot_ms"])
    first, done = record.get("t_first_token"), record.get("t_complete")
    out_tokens = record.get("output_tokens") or 0
    if first is None or done is None or out_tokens <= 1:
        return None
    return (done - first) * 1000.0 / (out_tokens - 1)


def classify(record: dict) -> str:
    """把记录的状态归类到四态之一（缺失或无法识别时按 failed 处理）。"""
    raw = (record.get("status") or "").strip().lower()
    if raw in ALL_STATUSES:
        return raw
    return {
        "ok": STATUS_SUCCESS, "completed": STATUS_SUCCESS, "done": STATUS_SUCCESS,
        "error": STATUS_FAILED, "fail": STATUS_FAILED, "failure": STATUS_FAILED,
        "canceled": STATUS_CANCELLED, "abort": STATUS_CANCELLED, "aborted": STATUS_CANCELLED,
        "timedout": STATUS_TIMEOUT, "time_out": STATUS_TIMEOUT,
    }.get(raw, STATUS_FAILED)


# ---------------------------------------------------------------------------
# 三、汇总
# ---------------------------------------------------------------------------

def summarize(
    records: list[dict],
    wall_clock_seconds: float | None = None,
    gpu: dict[str, Any] | None = None,
    kv: dict[str, Any] | None = None,
    config_version: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """把逐请求记录汇总为可直接写入 summary.json 的结构。"""
    total = len(records)
    status_counts = {name: 0 for name in ALL_STATUSES}
    for rec in records:
        status_counts[classify(rec)] += 1

    success_records = [r for r in records if classify(r) == STATUS_SUCCESS]

    ttfts = [v for v in (compute_ttft_ms(r) for r in success_records) if v is not None]
    e2es = [v for v in (compute_e2e_ms(r) for r in success_records) if v is not None]
    tpots = [v for v in (compute_tpot_ms(r) for r in success_records) if v is not None]
    output_tokens_total = sum(int(r.get("output_tokens") or 0) for r in success_records)

    window = wall_clock_seconds
    if window is None:
        starts = [r["t_submit"] for r in records if r.get("t_submit") is not None]
        ends = [r["t_complete"] for r in records if r.get("t_complete") is not None]
        window = (max(ends) - min(starts)) if starts and ends else None

    throughput = (output_tokens_total / window) if (window and window > 0) else None
    success_rate = (status_counts[STATUS_SUCCESS] / total) if total else None

    summary: dict[str, Any] = {
        "run_id": run_id,
        "config_version": config_version,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "totals": {"requests": total, **status_counts},
        "success_rate": _round(success_rate, 4),
        "ttft_ms": _spread(ttfts),
        "e2e_ms": _spread(e2es),
        "tpot_ms": {**_spread(tpots), "excludes_first_token": True},
        "throughput": {
            "output_tokens_total": output_tokens_total,
            "window_seconds": _round(window),
            "output_tokens_per_second": _round(throughput),
            "note": "仅统计成功请求的输出 token；窗口为最早提交至最晚完成",
        },
        "definitions": {
            "ttft": "客户端提交 → 首个含非空内容的流式块，含排队与传输",
            "tpot": "(完成 − 首 token) ÷ (输出 token 数 − 1)，排除首 token",
            "e2e": "完成 − 提交，含排队、提示词处理、生成与传输",
            "throughput": "成功请求的输出 token 总数 ÷ 统计窗口",
            "success_rate": "成功请求数 ÷ 全部请求数",
            "percentile": "最近秩法：位置 = ceil(k/100 × 样本数)",
        },
    }
    if gpu:
        summary["gpu"] = gpu
    if kv:
        summary["kv"] = kv
    return summary


def _spread(values: list[float]) -> dict[str, Any]:
    """给一组样本算 count/median/p95/mean/min/max。"""
    return {
        "count": len(values),
        "median": _round(median(values)),
        "p95": _round(percentile(values, 95)),
        "mean": _round(mean(values)),
        "min": _round(min(values)) if values else None,
        "max": _round(max(values)) if values else None,
    }


# ---------------------------------------------------------------------------
# 四、读写
# ---------------------------------------------------------------------------

def load_records(jsonl_path: str) -> list[dict]:
    """读取逐请求记录（JSONL，每行一个 JSON 对象）。"""
    if not os.path.isfile(jsonl_path):
        raise FileNotFoundError(f"找不到记录文件：{jsonl_path}")
    records = []
    with open(jsonl_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{jsonl_path} 第 {lineno} 行不是合法 JSON：{exc}") from exc
    return records


def write_summary(summary: dict, out_path: str) -> str:
    """写出 summary.json，返回绝对路径。"""
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# 五、自检：用构造数据验证公式
# ---------------------------------------------------------------------------

def _selfcheck() -> int:
    base = 1_000_000.0
    records: list[dict] = []

    # 8 条普通成功请求：TTFT=200ms、生成耗时 1.0s（TPOT = 1000/100 = 10 ms）
    for i in range(8):
        submit = base + i * 0.01
        first = submit + 0.20
        records.append({
            "request_id": f"R{i:02d}",
            "t_submit": submit,
            "t_first_token": first,
            "t_complete": first + 1.0,
            "output_tokens": 101,
            "status": "success",
        })

    # 2 条慢请求：TTFT=200ms、生成耗时 30.0s
    for i in range(8, 10):
        submit = base + i * 0.01
        first = submit + 0.20
        records.append({
            "request_id": f"R{i:02d}",
            "t_submit": submit,
            "t_first_token": first,
            "t_complete": first + 30.0,
            "output_tokens": 101,
            "status": "success",
        })

    records.append({"request_id": "R10", "status": "timeout", "t_submit": base + 0.5})
    records.append({"request_id": "R11", "status": "cancelled", "t_submit": base + 0.6})

    summary = summarize(records, run_id="SELFTEST", config_version="selfcheck")

    ok = True

    def check(name: str, got, want, tol: float = 1e-6) -> None:
        nonlocal ok
        good = got is not None and abs(float(got) - float(want)) <= tol
        ok = ok and good
        print(f"  [{'OK ' if good else 'FAIL'}] {name:<30} 得到 {got}，期望 {want}")

    print("=" * 70)
    print("metrics.py 自检（12 条记录：8 快 + 2 慢 + 1 超时 + 1 取消）")
    print("=" * 70)
    check("成功请求数", summary["totals"]["success"], 10)
    check("超时请求数", summary["totals"]["timeout"], 1)
    check("取消请求数", summary["totals"]["cancelled"], 1)
    check("成功率", summary["success_rate"], round(10 / 12, 4))
    # TTFT = 200ms（所有成功请求一致）
    check("TTFT 中位数(ms)", summary["ttft_ms"]["median"], 200.0)
    # 完成耗时 = TTFT + 生成耗时：8 条 1.2s、2 条 30.2s
    # 最近秩法：n=10，P50 → ceil(0.5×10)=5 → 第 5 个 = 1200
    check("完成耗时中位数(ms)", summary["e2e_ms"]["median"], 1200.0)
    # P95 → ceil(0.95×10)=10 → 第 10 个 = 30200
    check("完成耗时 P95(ms)", summary["e2e_ms"]["p95"], 30200.0)
    check("完成耗时 max(ms)", summary["e2e_ms"]["max"], 30200.0)
    check("TPOT 中位数(ms/token)", summary["tpot_ms"]["median"], 10.0)
    check("输出 token 合计", summary["throughput"]["output_tokens_total"], 1010)
    print(f"  [INFO] percentile([], 95) = {percentile([], 95)}（应为 None）")
    print(f"  [INFO] percentile([5], 95) = {percentile([5], 95)}（应为 5）")
    print("-" * 70)
    print(f"统计窗口: {round(summary['throughput']['window_seconds'], 3)} 秒")
    print(f"吞吐: {round(summary['throughput']['output_tokens_per_second'], 3)} token/s")
    print(f"整体: {'全部通过' if ok else '存在不符，请检查'}")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selfcheck())
