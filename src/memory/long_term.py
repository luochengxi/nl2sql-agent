"""长期记忆 + 查询历史（F6）。

跨会话保留：用户常用业务线、查询历史（可复用/重跑）。
用 JSON 文件落盘，零外部依赖；生产环境可平替为 Redis/DB。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime

from ..core.config import ROOT

_PATH = os.path.join(ROOT, "logs", "long_term.json")
_LOCK = threading.Lock()


def _load() -> dict:
    if not os.path.exists(_PATH):
        return {}
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_query(user_id: str, query: str, plan: dict, sql: str | None) -> str:
    qid = datetime.now().strftime("%Y%m%d%H%M%S%f")
    with _LOCK:
        data = _load()
        user = data.setdefault(user_id, {"preferences": {}, "history": []})
        user["history"].insert(0, {
            "query_id": qid,
            "ts": datetime.now().isoformat(timespec="seconds"),
            "query": query,
            "plan": plan,
            "sql": sql,
        })
        user["history"] = user["history"][:100]
        bl = plan.get("business_line")
        if bl:
            prefs = user["preferences"]
            prefs["business_line_count"] = prefs.get("business_line_count", {})
            prefs["business_line_count"][bl] = prefs["business_line_count"].get(bl, 0) + 1
        _save(data)
    return qid


def history(user_id: str, limit: int = 20) -> list[dict]:
    return (_load().get(user_id, {}).get("history") or [])[:limit]


def preferences(user_id: str) -> dict:
    return _load().get(user_id, {}).get("preferences", {})


def top_business_line(user_id: str) -> str | None:
    counts = preferences(user_id).get("business_line_count", {})
    return max(counts, key=counts.get) if counts else None


def record_feedback(user_id: str, query_id: str, rating: str, comment: str = "",
                    corrected_sql: str | None = None) -> None:
    """F7 反馈闭环：赞/踩 + 纠正 SQL，沉淀为后续优化素材。"""
    with _LOCK:
        data = _load()
        for h in data.get(user_id, {}).get("history", []):
            if h.get("query_id") == query_id:
                h["feedback"] = {
                    "rating": rating,
                    "comment": comment,
                    "corrected_sql": corrected_sql,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                break
        _save(data)
