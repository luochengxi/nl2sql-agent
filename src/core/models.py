"""全项目共享的数据结构。

QueryPlan 是本项目的"枢纽契约"（见技术方案拆解 §4）：
理解侧（LLM/Mock）只负责产出它，执行侧（编译器/权限/校验）只消费它。
两侧可以独立演进、独立测试。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Filter(BaseModel):
    column: str
    op: Literal["=", "IN", "LIKE"] = "="
    value: str


class TimeRange(BaseModel):
    start: str
    end: str
    preset: str | None = None
    compare_type: str | None = None          # mom | wow | dod | yoy
    compare_start: str | None = None
    compare_end: str | None = None


class QueryPlan(BaseModel):
    raw_query: str = ""
    business_line: str | None = None
    metrics: list[str] = Field(default_factory=list)       # 语义层 metric id
    dimensions: list[str] = Field(default_factory=list)    # 语义层 dimension id
    time_range: TimeRange | None = None
    filters: list[Filter] = Field(default_factory=list)
    metric_candidates: list[str] = Field(default_factory=list)  # 歧义候选 -> 触发澄清
    confidence: float = 0.90
    missing: list[str] = Field(default_factory=list)
    caliber_refs: list[str] = Field(default_factory=list)
    explanation: str = ""                  # "问题 -> SQL" 的映射说明
    inherited: bool = False                # 槽位是否来自上轮记忆
    clarification_question: str | None = None


class UserContext(BaseModel):
    """权限画像：一次请求只解析一次，后续所有模块只读它。"""
    user_id: str
    name: str
    role: str
    role_label: str
    scope: str                              # all | region | city | store
    scope_values: dict[str, str] = Field(default_factory=dict)
    column_policy: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    user_id: str
    query: str
    session_id: str | None = None
    business_line: str | None = None   # F11：显式指定/切换业务线


class ChatResponse(BaseModel):
    session_id: str
    answer_type: Literal["result", "clarify", "fallback", "error"] = "result"
    text: str = ""
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    chart: dict[str, Any] | None = None
    confidence: float = 0.0
    plan: dict[str, Any] | None = None
    caliber_refs: list[str] = Field(default_factory=list)
    explanation: str = ""
    export_token: str | None = None
    drilldown_token: str | None = None
    dim_columns: list[str] = Field(default_factory=list)  # 前端表格下钻用的物理列名
    suggestions: list[str] = Field(default_factory=list)
    trace_id: str = ""


class AuditEvent(BaseModel):
    ts: str
    user_id: str
    session_id: str
    action: str
    detail: dict[str, Any] = Field(default_factory=dict)
