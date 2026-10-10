from datetime import datetime
import json


STATUSES = ("open", "processing", "closed", "other")


def parse_time(value):
    """将时间字符串转换为 datetime。"""
    return datetime.fromisoformat(value) if value else None


def ticket_hours(record):
    """读取一条工单的处理耗时，单位为小时。"""
    start = parse_time(record["created_at"])
    end = parse_time(record["closed_at"])
    if start and end and end >= start:
        return (end - start).total_seconds() / 3600
    return 0


def summarize_tickets(records):
    """按状态、处理人汇总，并计算平均处理耗时。"""
    by_status = {name: 0 for name in STATUSES}
    by_owner = {}
    hours = []

    for record in records:
        id = record.get("id").strip()
        owner = record.get("owner")
        status = record["status"].strip().lower()
        if not id or owner in ["", None] or status not in STATUSES:
            continue

        if status in by_status:
            by_status[status] += 1
        else:
            by_status[status] = 1

        if id not in by_owner:
            by_owner[id] = {
                "total": 0,
                "open": 0,
                "processing": 0,
                "closed": 0,
                "other": 0,
            }

        by_owner[id]["total"] += 1
        by_owner[id][status] += 1

        if record.get("closed_at"):
            hours.append(ticket_hours(record))
        else:
            hours.append(0)

    average = sum(hours) / len(hours) if hours else None

    return {
        "total": len(records),
        "by_status": by_status,
        "by_owner": by_owner,
        "average_closed_hours": average,
    }


def render_summary(summary):
    """将统计结果转换为可打印的文本。"""
    lines = ["工单统计", f"有效工单总数：{summary['total']}"]
    lines.append("状态分布：")
    for status in STATUSES:
        lines.append(f"  {status}: {summary['by_status'][status]}")
    lines.append("处理人分布：")
    for owner, counts in sorted(summary["by_owner"].items()):
        lines.append(f"  {owner}: {counts['total']}")
    average = summary["average_closed_hours"]
    lines.append(f"平均已关闭工单耗时：{average} 小时" if average else "无有效已关闭工单耗时")
    return "\n".join(lines)


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


# 以下情境供测试设计时参考，可自行构造更多组合。
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


def main():
    summary = summarize_tickets(SAMPLE_RECORDS)
    print(render_summary(summary))
    print("\n结构化结果：")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
