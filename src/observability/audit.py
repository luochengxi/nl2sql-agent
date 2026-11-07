"""审计日志（F8）：谁、何时、问了什么、生成了什么 SQL、导出了什么。

append-only JSONL，简单可靠；合规要求留存期由 settings.audit.retention_days 声明。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime

from ..core.config import audit_path, settings
from ..core.models import AuditEvent
from ..defects import registry as _defects

_LOCK = threading.Lock()


def log(user_id: str, session_id: str, action: str, **detail) -> None:
    if not settings().get("audit", {}).get("enabled", True):
        return
    # [DEFECT-ID: D18] 部分操作（导出/澄清/反馈）未留痕
    if _defects.on("D18") and action in ("export", "clarify", "feedback", "drilldown"):
        return
    event = AuditEvent(
        ts=datetime.now().isoformat(timespec="seconds"),
        user_id=user_id,
        session_id=session_id,
        action=action,
        detail=detail,
    )
    path = audit_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event.model_dump(), ensure_ascii=False) + "\n")


def read(limit: int = 200) -> list[dict]:
    path = audit_path()
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
