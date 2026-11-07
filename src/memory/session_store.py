"""短期记忆：当前会话的槽位与对话轮次。

为什么要和长期记忆分开（技术方案拆解 §8）：
短期记忆是"这一轮对话的上下文"，生命周期是会话级、会过期；
长期记忆是"跨会话的用户偏好与历史"。混在一起会让上个月的筛选条件
污染今天的问题，或者让偏好在会话结束时丢失。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..core.config import settings
from ..defects import registry as _defects


@dataclass
class Turn:
    query: str
    answer_type: str
    plan: dict = field(default_factory=dict)
    ts: str = ""


@dataclass
class Session:
    session_id: str
    user_id: str
    slots: dict = field(default_factory=dict)
    turns: list[Turn] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def expired(self) -> bool:
        ttl = int(settings().get("session", {}).get("ttl_minutes", 60))
        return datetime.now() - self.updated_at > timedelta(minutes=ttl)


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, user_id: str) -> str:
        sid = uuid.uuid4().hex[:16]
        self._sessions[sid] = Session(session_id=sid, user_id=user_id)
        return sid

    def get(self, session_id: str, user_id: str) -> Session | None:
        s = self._sessions.get(session_id)
        if s is None or s.expired():
            return None
        # [DEFECT-ID: D14] 会话归属校验缺失：不同账号可共用同一个 session_id
        if not _defects.on("D14") and s.user_id != user_id:
            return None
        return s

    def touch(self, s: Session, slots: dict | None = None, turn: Turn | None = None) -> None:
        s.updated_at = datetime.now()
        if slots is not None:
            s.slots = slots
        if turn is not None:
            s.turns.append(turn)
            max_turns = int(settings().get("session", {}).get("max_history_turns", 10))
            s.turns = s.turns[-max_turns:]

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


_store: SessionStore | None = None


def get_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store
