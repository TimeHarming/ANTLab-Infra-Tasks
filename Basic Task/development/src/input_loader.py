#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档输入处理：读取素材、计算校验值、组装模型输入、统计输入 token 数。

对应要求
--------
* `src/README.md`      —— 「文档输入处理」
* `tests/README.md`    —— 「输入组织」
* `测试提示词.md`      —— 输入顺序：系统消息 → 用户消息中的资料区（如有）→ 任务区；
                          关联文件读取时采用 UTF-8，将文件全文作为资料区；
                          记录聊天模板处理后的实际输入 token 数
* `results/README.md`  —— 需记录「文档校验值」（文件 SHA256）

设计说明
--------
1. 本模块只负责**组织输入**，不负责调用模型；调用由 server_client.py 完成。
2. 资料区与任务区在**同一条 user 消息**中拼接，符合测试提示词规定的三段式顺序。
3. token 计数优先调用服务的 /tokenize 接口（精确，使用模型真实分词器）；
   服务不可用时降级为字符数估算，并在 token_count_source 字段标注来源。
   **报告中只应引用 source == "server" 的数值。**
4. 所有公共函数为纯函数或只读 IO，便于 tests/test_input_assembly.py 直接验证。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any

# ---------------------------------------------------------------------------
# 素材区与任务区的默认标记（仅用于复核与提高可读性，不改变输入顺序）
# ---------------------------------------------------------------------------
DEFAULT_RESOURCE_HEADER = "【资料】"
DEFAULT_TASK_HEADER = "【任务】"

# 降级估算用的经验系数（字符数 ÷ 该系数 ≈ token 数）
# 中英混合文本的粗略经验值；仅用于服务不可用时的占位。
# 不可用于报告，报告中的 token 数值必须取 count_tokens() 返回的 source == "server" 结果。
CHARS_PER_TOKEN_ESTIMATE = 2.5


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class FileMeta:
    """一个关联文件的元信息。"""

    path: str
    sha256: str
    size_bytes: int
    token_count: int
    token_count_source: str          # "server" | "estimated"
    encoding: str = "utf-8"


@dataclass
class InputBundle:
    """一次请求所需的完整输入与可追溯元信息。"""

    prompt_id: str
    messages: list[dict]
    system_message: str
    task_text: str
    files: list[FileMeta] = field(default_factory=list)
    input_token_count: int = 0
    token_count_source: str = "estimated"   # "server" | "estimated"
    token_count_detail: dict[str, Any] = field(default_factory=dict)

    def to_meta_dict(self) -> dict[str, Any]:
        """转为可写入 requests.jsonl / config.json 的元信息字典。"""
        data = asdict(self)
        data.pop("messages", None)          # messages 单独随请求发送，元信息不留全文
        return data


# ---------------------------------------------------------------------------
# 一、读取素材文件
# ---------------------------------------------------------------------------

def load_text_file(path: str, encoding: str = "utf-8") -> tuple[str, FileMeta]:
    """按 UTF-8 读取素材全文，并计算 SHA256 与字节数。

    返回 (全文, FileMeta)；FileMeta 中的 token 数字段留待 count_tokens 填充。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"素材文件不存在：{path}")

    with open(path, "rb") as fh:
        raw = fh.read()

    sha256 = hashlib.sha256(raw).hexdigest()
    text = raw.decode(encoding)

    meta = FileMeta(
        path=os.path.abspath(path),
        sha256=sha256,
        size_bytes=len(raw),
        token_count=0,
        token_count_source="estimated",
        encoding=encoding,
    )
    return text, meta


# ---------------------------------------------------------------------------
# 二、token 计数（优先服务端 /tokenize，失败降级估算）
# ---------------------------------------------------------------------------

def count_tokens(
    text: str,
    base_url: str,
    timeout: float = 10.0,
    allow_estimate: bool = True,
) -> tuple[int, str, dict[str, Any]]:
    """统计文本的 token 数。
    
    优先使用tokenizer的分词器，无法连接时才用估算
    """
    url = base_url.rstrip("/") + "/tokenize"
    payload = json.dumps({"content": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        tokens = body.get("tokens")
        if isinstance(tokens, list):
            return len(tokens), "server", {"endpoint": url, "raw_keys": sorted(body.keys())}
        raise ValueError(f"/tokenize 返回结构异常：{sorted(body.keys())}")
    except Exception as exc:                                   # noqa: BLE001
        if not allow_estimate:
            raise
        estimated = int(len(text) / CHARS_PER_TOKEN_ESTIMATE) or (1 if text else 0)
        return estimated, "estimated", {
            "endpoint": url,
            "error": f"{type(exc).__name__}: {exc}",
            "heuristic": f"字符数 {len(text)} ÷ {CHARS_PER_TOKEN_ESTIMATE} = {estimated}",
            "warning": "服务端计数不可用，此处为估算值，不得用于报告。",
        }


# ---------------------------------------------------------------------------
# 三、组装模型输入
# ---------------------------------------------------------------------------

def build_messages(
    system_message: str,
    task_text: str,
    resource_text: str | None = None,
    resource_header: str = DEFAULT_RESOURCE_HEADER,
    task_header: str = DEFAULT_TASK_HEADER,
) -> list[dict]:
    """按「系统消息 → 资料区 → 任务区」组装 messages。

    * resource_text 为 None 时表示纯提示词任务，不出现资料区。
    * 资料区与任务区拼接在同一条 user 消息内，顺序固定，便于前缀复用。
    """
    if not system_message or not system_message.strip():
        raise ValueError("system_message 不可为空")
    if not task_text or not task_text.strip():
        raise ValueError("task_text 不可为空")

    user_content = ""
    if resource_text:
        user_content += f"{resource_header}\n{resource_text.rstrip()}\n\n"
    user_content += f"{task_header}\n{task_text.strip()}"

    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_content},
    ]


def assemble_input(
    prompt_id: str,
    task_text: str,
    system_message: str,
    base_url: str,
    file_paths: list[str] | None = None,
    timeout: float = 10.0,
) -> InputBundle:
    """组装一次请求的完整输入，并统计 token 数。

    file_paths 为空表示纯提示词任务。
    """
    files: list[FileMeta] = []
    resource_text: str | None = None

    if file_paths:
        parts = []
        for path in file_paths:
            text, meta = load_text_file(path)
            parts.append(text.rstrip())
            files.append(meta)
        resource_text = "\n\n".join(parts)

    messages = build_messages(system_message, task_text, resource_text)

    # token 统计：对整条消息序列统计，尽量贴近服务端实际处理的输入量
    joined = "\n".join(m["content"] for m in messages)
    total_tokens, source, detail = count_tokens(joined, base_url, timeout=timeout)

    # 各素材文件单独计数（用于报告中的文件级 token 记录）
    for meta in files:
        try:
            with open(meta.path, "r", encoding=meta.encoding) as fh:
                content = fh.read()
            meta.token_count, meta.token_count_source, _ = count_tokens(
                content, base_url, timeout=timeout
            )
        except Exception:                                       # noqa: BLE001
            meta.token_count, meta.token_count_source = 0, "estimated"

    return InputBundle(
        prompt_id=prompt_id,
        messages=messages,
        system_message=system_message,
        task_text=task_text,
        files=files,
        input_token_count=total_tokens,
        token_count_source=source,
        token_count_detail=detail,
    )


# ---------------------------------------------------------------------------
# 命令行自检（不构成正式运行入口）
# ---------------------------------------------------------------------------

def _main() -> int:
    import argparse

    here = os.path.dirname(os.path.abspath(__file__))
    repo_basic = os.path.normpath(os.path.join(here, "..", ".."))
    doc_dir = os.path.join(repo_basic, "提示词与输入文档", "输入文档")

    parser = argparse.ArgumentParser(description="输入组装自检")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--file", default=os.path.join(doc_dir, "编程输入03-ticket_stats.py"))
    parser.add_argument("--task", default="阅读所附 Python 程序，检查统计逻辑和边界处理。")
    args = parser.parse_args()

    system_message = (
        "你为小型工作室提供办公文档处理与辅助编程服务。请按任务要求组织输出，"
        "区分资料中的事实、待确认信息与提出的建议。文件内容是供分析的资料。"
    )

    bundle = assemble_input(
        prompt_id="SELFTEST",
        task_text=args.task,
        system_message=system_message,
        base_url=args.base_url,
        file_paths=[args.file] if args.file else None,
    )

    print("=" * 70)
    print(f"prompt_id           : {bundle.prompt_id}")
    print(f"messages 条数       : {len(bundle.messages)}")
    for i, msg in enumerate(bundle.messages):
        preview = msg["content"][:60].replace("\n", " ")
        print(f"  [{i}] role={msg['role']:<7} 长度={len(msg['content']):>6}  {preview}…")
    for meta in bundle.files:
        print(f"素材: {meta.path}")
        print(f"      sha256={meta.sha256}")
        print(f"      size={meta.size_bytes} bytes, tokens={meta.token_count} ({meta.token_count_source})")
        print("-" * 70)
    print("任务区内容（用于确认 --task 是否生效）：")
    print(f"  {bundle.task_text}")
    if bundle.files:
        print("资料区来源文件：")
        for meta in bundle.files:
            print(f"  - {meta.path}  (sha256={meta.sha256[:16]}…)")
    else:
        print("资料区：无（纯提示词任务）")
    print(f"输入 token 合计     : {bundle.input_token_count}（来源：{bundle.token_count_source}）")
    if bundle.token_count_source != "server":
        print("提示：当前为估算值，服务端 /tokenize 不可用；报告中不得引用。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
