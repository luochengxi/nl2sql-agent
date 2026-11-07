"""轻量运行指标（可观测）：查询量、澄清率、兜底率、平均耗时。

为什么值得单独记：澄清率过高说明语义层/引导没做好；
兜底率过高说明意图识别不准。它们是评测体系的核心输入。
"""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_COUNTERS: dict[str, int] = {}
_LATENCY: list[float] = []


def incr(name: str, value: int = 1) -> None:
    with _LOCK:
        _COUNTERS[name] = _COUNTERS.get(name, 0) + value


def observe_latency(ms: float) -> None:
    with _LOCK:
        _LATENCY.append(ms)
        if len(_LATENCY) > 500:
            _LATENCY.pop(0)


def snapshot() -> dict:
    with _LOCK:
        c = dict(_COUNTERS)
        lat = list(_LATENCY)
    total = c.get("query", 0)
    return {
        "counters": c,
        "total_queries": total,
        "clarify_rate": round(c.get("clarify", 0) / total, 3) if total else 0.0,
        "fallback_rate": round(c.get("fallback", 0) / total, 3) if total else 0.0,
        "avg_latency_ms": round(sum(lat) / len(lat), 1) if lat else 0.0,
    }
