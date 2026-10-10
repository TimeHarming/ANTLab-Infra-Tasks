#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单统计程序（参考答案实现）。

用途
----
本文件是 `提示词与输入文档/输入文档/编程输入03-ticket_stats.py` 的**正确实现**，
用于给 `tests/verify_ticket_stats.py` 的 14 个判定用例做背书：

* 回答“这些用例本身是否自洽” —— 参考答案应 14/14 全部通过；
* 作为模型输出效果的**对照基准** —— 例如“模型修复版 1/14 vs 参考实现 14/14”。

注意：本文件是**评测基准**，不是模型输出，也不属于被测素材。
所有行为严格依据待审查版本文件头部《功能约定》：

    id         ：去除两端空格后判断；缺失或为空则跳过该记录。
    owner      ：缺失、None 或仅含空格时归为 unassigned。
    status     ：去除两端空格并忽略大小写；缺失或其他值归为 other。
    created_at ：ISO 8601 创建时间，例如 2026-10-12T09:00:00+08:00。
    closed_at  ：ISO 8601 关闭时间，可以缺失。

    同一编号出现多次时，只统计第一条具有该有效编号的记录。
    total 为去重后有效编号的记录数。by_status 始终包含 open、processing、
    closed、other 四个键。by_owner 使用规范化后的处理人，每人的记录包含
    total 和上述四种状态的数量。

    平均处理耗时仅采用 closed 工单：创建和关闭时间均可解析、均含时区，
    且关闭时间晚于或等于创建时间。以小时计算，结果保留两位小数。
    无效时间仍保留工单计数，只跳过该工单的耗时统计。
    没有可用于计算的 closed 工单时，average_closed_hours 为 None。
    空输入返回 total=0、四种状态均为 0、by_owner={}、平均耗时为 None。

主要函数及调用方式（保留与待审查版本一致的名称）：
    parse_time(value)
    ticket_hours(record)
    summarize_tickets(records)
    render_summary(summary)
    main()

运行环境：Python 3.10 及以上，仅使用标准库。
"""

from __future__ import annotations

from datetime import datetime, tzinfo
import json
from typing import Any

STATUSES = ("open", "processing", "closed", "other")

UNASSIGNED = "unassigned"


# ---------------------------------------------------------------------------
# 时间与字段规范化
# ---------------------------------------------------------------------------

def parse_time(value: Any) -> datetime | None:
    """将时间字符串转换为 datetime；无法解析或为空时返回 None。

    与待审查版本的差异：不抛异常，且把“缺少时区”也视为不可用
    （约定要求“均含时区”才计入耗时），因此无时区的值一律返回 None。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None

    # 约定：创建与关闭时间“均含时区”才可参与耗时计算。
    return parsed if parsed.tzinfo is not None else None


def normalize_id(value: Any) -> str:
    """规范化工单编号；无效时返回空字符串。"""
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    # 非字符串编号（如数字）转成字符串后再判断，避免崩溃。
    return str(value).strip()


def normalize_owner(value: Any) -> str:
    """规范化处理人；缺失、None 或仅含空格时归为 unassigned。"""
    if value is None:
        return UNASSIGNED
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else UNASSIGNED
    return str(value).strip() or UNASSIGNED


def normalize_status(value: Any) -> str:
    """规范化状态；去除两端空格、忽略大小写，未知或缺失归为 other。"""
    if isinstance(value, str):
        normalized = value.strip().lower()
    elif value is None:
        normalized = ""
    else:
        normalized = str(value).strip().lower()
    return normalized if normalized in STATUSES else "other"


def ticket_hours(record: dict) -> float | None:
    """计算一条工单的处理耗时（小时）。

    仅当创建与关闭时间均可解析、均含时区，且关闭时间不早于创建时间时返回数值；
    否则返回 None（调用方负责保留计数、跳过耗时）。
    """
    start = parse_time(record.get("created_at"))
    end = parse_time(record.get("closed_at"))
    if start is None or end is None:
        return None
    if end < start:
        return None
    return (end - start).total_seconds() / 3600


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def _new_owner_bucket() -> dict:
    bucket = {"total": 0}
    bucket.update({name: 0 for name in STATUSES})
    return bucket


def summarize_tickets(records) -> dict:
    """按状态、处理人汇总，并计算平均处理耗时。"""
    by_status = {name: 0 for name in STATUSES}
    by_owner: dict = {}
    hours: list[float] = []
    seen_ids: set[str] = set()
    valid_count = 0

    for record in records or []:
        if not isinstance(record, dict):
            continue

        # 约定：编号缺失或为空的记录跳过。
        ticket_id = normalize_id(record.get("id"))
        if not ticket_id:
            continue

        # 约定：同一编号重复出现时，只统计第一条。
        if ticket_id in seen_ids:
            continue
        seen_ids.add(ticket_id)

        owner = normalize_owner(record.get("owner"))
        status = normalize_status(record.get("status"))

        valid_count += 1
        by_status[status] += 1

        bucket = by_owner.setdefault(owner, _new_owner_bucket())
        bucket["total"] += 1
        bucket[status] += 1

        # 仅 closed 工单参与耗时统计；无效时间保留计数、跳过耗时。
        if status == "closed":
            value = ticket_hours(record)
            if value is not None:
                hours.append(value)

    if hours:
        average: float | None = round(sum(hours) / len(hours), 2)
    else:
        average = None

    return {
        "total": valid_count,
        "by_status": by_status,
        "by_owner": by_owner,
        "average_closed_hours": average,
    }


def render_summary(summary: dict) -> str:
    """将统计结果转换为可打印的文本。"""
    lines = ["工单统计", f"有效工单总数：{summary['total']}"]
    lines.append("状态分布：")
    for status in STATUSES:
        lines.append(f"  {status}: {summary['by_status'][status]}")
    lines.append("处理人分布：")
    for owner, counts in sorted(summary["by_owner"].items()):
        lines.append(f"  {owner}: {counts['total']}")
    average = summary["average_closed_hours"]
    if average is None:
        lines.append("无有效已关闭工单耗时")
    else:
        lines.append(f"平均已关闭工单耗时：{average} 小时")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 示例与入口（保留自待审查版本，便于人工对照）
# ---------------------------------------------------------------------------

SAMPLE_RECORDS = [
    {
        "id": "T001",
        "owner": "Chen",
        "status": "closed",
        "created_at": "2026-10-12T09:00:00+08:00",
        "closed_at": "2026-10-12T11:30:00+08:00",
    },
    {
        "id": "T002",
        "owner": "Song",
        "status": "processing",
        "created_at": "2026-10-12T10:00:00+08:00",
        "closed_at": None,
    },
    {
        "id": "T003",
        "owner": "Chen",
        "status": "open",
        "created_at": "2026-10-12T13:00:00+08:00",
        "closed_at": None,
    },
    {
        "id": "T004",
        "owner": "Zhou",
        "status": "closed",
        "created_at": "2026-10-12T14:00:00+08:00",
        "closed_at": "2026-10-12T18:15:00+08:00",
    },
    {
        "id": "T001",
        "owner": "Chen",
        "status": "closed",
        "created_at": "2026-10-12T09:00:00+08:00",
        "closed_at": "2026-10-12T11:30:00+08:00",
    },
]

EDGE_SCENARIOS = (
    "输入为空列表",
    "有效工单缺少 owner、status 或 closed_at",
    "处理人为空字符串、None 或只含空格",
    "状态含首尾空格、大小写变化或未知值",
    "相同 id 重复出现，以及 id 缺失或只含空格",
    "未关闭工单带有遗留的 closed_at 字段",
    "关闭工单缺少时间或时间格式错误",
    "创建时间和关闭时间使用不同的 UTC 偏移",
    "时间缺少时区，以及关闭时间早于创建时间",
    "输入仅包含没有有效耗时的工单",
)


def main() -> None:
    summary = summarize_tickets(SAMPLE_RECORDS)
    print(render_summary(summary))
    print("\n结构化结果：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
