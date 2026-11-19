"""执行链路 Trace：模型决策 + 工具执行双层留痕。

一次 /api/chat-fc 请求 = 一个 turn，turn 内有两种记录：
- decision：LLM 每轮想调用哪些工具（决策证据）
- tool_call：dispatcher 实际执行了什么（执行证据）

分开记的原因：测试要同时回答两个问题——
"模型选对了没"（看 decision）和"执行对了没"（看 tool_call）。
和 audit.py 的区别：audit 记业务事实（谁问了什么、导出了什么），
trace 记过程（选了什么工具、参数对不对、调了几次），专供评测断言。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from collections import defaultdict
from datetime import datetime

from ..core.config import ROOT, settings

_LOCK = threading.Lock()
_TURNS: dict[str, list[dict]] = defaultdict(list)
_RECENT: list[dict] = []
_MAX = 2000


def new_turn() -> str:
    """生成一个 turn_id，随响应返回给测试，用来捞这次请求的 trace。"""
    return uuid.uuid4().hex[:12]


def trace_path() -> str:
    return os.path.join(
        ROOT, settings().get("audit", {}).get("trace_path", "logs/trace.jsonl")
    )


def _write(turn_id: str, rec: dict) -> None:
    """统一写入：内存索引 + 滚动缓冲 + 落盘 JSONL。"""
    with _LOCK:
        _TURNS[turn_id].append(rec)
        _RECENT.append(rec)
        if len(_RECENT) > _MAX:
            _RECENT.pop(0)
        path = trace_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def decision(turn_id: str, round_no: int, raw_calls: list[dict],
             finish_reason: str | None) -> None:
    """记录 LLM 这一轮想调什么（决策证据）。"""
    _write(turn_id, {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "turn_id": turn_id,
        "kind": "decision",
        "round": round_no,
        "selected": [c.get("name") for c in raw_calls],
        "raw": raw_calls,
        "finish_reason": finish_reason,
    })


def tool_call(turn_id: str, round_no: int, call_id: str | None, name: str | None,
              args: dict, result: dict, status: str, latency_ms: float,
              error: str | None = None) -> None:
    """记录 dispatcher 实际执行了什么（执行证据）。

    result 只放摘要（row_count / sql / doc_count），不放全量 rows——
    否则一次取数几万行会把 trace 撑爆，测试也用不上。
    """
    _write(turn_id, {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "turn_id": turn_id,
        "kind": "tool_call",
        "round": round_no,
        "call_id": call_id,
        "tool_name": name,
        "tool_args": args,
        "tool_return": result,
        "status": status,           # ok | empty | error
        "latency_ms": round(latency_ms, 2),
        "error": error,
    })


def turn(turn_id: str) -> list[dict]:
    """取一个 turn 的全部 trace，测试主入口。"""
    with _LOCK:
        return [dict(r) for r in _TURNS.get(turn_id, [])]


def recent(n: int = 200) -> list[dict]:
    """取最近的记录，排查用。"""
    with _LOCK:
        return [dict(r) for r in _RECENT[-n:]]


def reset() -> None:
    """清空内存缓冲，测试隔离用（落盘文件不动）。"""
    with _LOCK:
        _TURNS.clear()
        _RECENT.clear()
