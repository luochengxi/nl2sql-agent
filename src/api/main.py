"""FastAPI 接入层。

只做三件事：参数校验、调编排层/能力层、把结果包成 HTTP 响应。
业务逻辑一律不写在这里——这样换 UI 或换协议时不用改核心。
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core import caliber_admin
from ..core.config import ROOT, business_lines, settings, today
from ..core.drilldown import build_detail_sql
from ..core.executor import run_raw
from ..core.models import ChatRequest, ChatResponse
from ..core.orchestrator import chat
from ..core.permission import list_users
from ..core.rag import get_index
from ..core.validator import SecurityError
from ..defects.registry import get_registry
from ..export.exporter import export
from ..memory import long_term
from ..observability import audit, metrics

app = FastAPI(
    title="零售取数 Agent",
    description="面向商户/运营/数据分析的 NL2SQL 取数 Agent（v1.1）",
    version="1.1",
)


@app.get("/api/health")
def health():
    return {"status": "ok", "today": today().isoformat(),
            "llm_provider": settings()["llm"]["provider"],
            "language": settings()["app"]["language"]}


@app.get("/api/config")
def get_config():
    reg = get_registry()
    return {
        "today": today().isoformat(),
        "language": settings()["app"]["language"],
        "llm_provider": settings()["llm"]["provider"],
        "defect_mode_enabled": reg.enabled,
        "active_defects": reg.active_ids(),      # 仅 id，不含位置
        "export_formats": settings()["export"]["formats"],
    }


@app.get("/api/users")
def users():
    return list_users()


@app.get("/api/business-lines")
def bl_list():
    return [
        {"id": k, "label": v["label"], "desc": v.get("desc"),
         "view": v["view"], "default_metric": v.get("default_metric")}
        for k, v in business_lines()["business_lines"].items()
    ]


@app.post("/api/chat", response_model=ChatResponse)
def api_chat(req: ChatRequest):
    return chat(req)


# ---------------- C6 导出 ----------------
@app.get("/api/export")
def api_export(token: str = Query(...), user_id: str = Query(...), fmt: str = Query("csv")):
    if fmt not in settings()["export"]["formats"]:
        raise HTTPException(400, f"不支持的导出格式: {fmt}")
    try:
        filename, content = export(token, user_id, fmt)
    except (KeyError, PermissionError) as e:
        raise HTTPException(403, str(e)) from e
    audit.log(user_id, "", "export", token=token, fmt=fmt, rows=filename)
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx" else "text/csv"
    # HTTP 头只能 latin-1：ASCII 兜底名 + RFC 5987 编码的中文名
    from urllib.parse import quote
    ascii_name = f"export.{fmt}"
    disp = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    return Response(content=content, media_type=media,
                    headers={"Content-Disposition": disp})


# ---------------- F10 下钻 ----------------
class DrilldownRequest(BaseModel):
    user_id: str
    token: str
    row_filters: dict[str, str] = {}


@app.post("/api/drilldown")
def api_drilldown(req: DrilldownRequest):
    from ..core.permission import load_user
    try:
        user = load_user(req.user_id)
        sql, params = build_detail_sql(req.token, user, req.row_filters)
        result = run_raw(sql, params)
    except (KeyError, PermissionError, SecurityError) as e:
        raise HTTPException(403, str(e)) from e
    audit.log(req.user_id, "", "drilldown", token=req.token, row_count=len(result.rows))
    return {"columns": result.columns, "rows": result.rows,
            "sql": sql, "elapsed_ms": result.elapsed_ms}


# ---------------- F6 查询历史 / F7 反馈 ----------------
@app.get("/api/history")
def api_history(user_id: str = Query(...), limit: int = 20):
    return long_term.history(user_id, limit)


class FeedbackRequest(BaseModel):
    user_id: str
    query_id: str
    rating: str
    comment: str = ""
    corrected_sql: str | None = None


@app.post("/api/feedback")
def api_feedback(req: FeedbackRequest):
    long_term.record_feedback(req.user_id, req.query_id, req.rating,
                              req.comment, req.corrected_sql)
    audit.log(req.user_id, "", "feedback", query_id=req.query_id, rating=req.rating)
    return {"ok": True}


# ---------------- F8 审计 / 可观测 ----------------
@app.get("/api/audit")
def api_audit(limit: int = 200):
    return audit.read(limit)


@app.get("/api/metrics")
def api_metrics():
    return metrics.snapshot()

@app.post("/api/chat-fc", response_model=ChatResponse)
def api_chat_fc(req: ChatRequest):
    from ..core.orchestrator import chat_fc
    return chat_fc(req)



@app.get("/api/trace/{turn_id}")
def api_trace(turn_id: str):
    from ..observability import trace
    return trace.turn(turn_id)



# ---------------- F16 口径管理后台 ----------------
@app.get("/api/calibers")
def api_calibers():
    idx = get_index()
    return [{"filename": d.filename, "metric": d.metric, "business_line": d.business_line,
             "owner": d.owner, "version": d.version, "updated": d.updated,
             "status": d.status, "summary": d.summary(120)} for d in idx.list_all()]


@app.get("/api/calibers/{filename}")
def api_caliber_detail(filename: str):
    try:
        return caliber_admin.read(filename)
    except (KeyError, ValueError) as e:
        raise HTTPException(404, str(e)) from e


class CaliberUpsert(BaseModel):
    filename: str
    metric: str
    business_line: str = "all"
    owner: str
    version: str = "1.0"
    body: str
    status: str = "active"


@app.post("/api/calibers")
def api_caliber_upsert(req: CaliberUpsert):
    try:
        saved = caliber_admin.upsert(
            req.filename, req.metric, req.business_line, req.owner,
            req.version, req.body, req.status,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # 关键：文档变更后必须重建索引，否则检索命中的还是旧版本
    count = get_index().refresh()
    audit.log("产品/运营", "", "caliber_update", filename=req.filename,
              version=req.version, owner=req.owner)
    return {"ok": True, "saved": saved, "indexed_docs": count}


@app.post("/api/calibers/refresh")
def api_caliber_refresh():
    return {"ok": True, "indexed_docs": get_index().refresh()}


# ---------------- 教学：缺陷分类清单（正常模式下不泄题） ----------------
@app.get("/api/defects")
def api_defects():
    reg = get_registry()
    if not reg.enabled:
        return {"enabled": False, "catalog": {}, "active": []}
    return {"enabled": True, "catalog": reg.catalog(), "active": reg.active_ids()}


# ---------------- 独立对话页（v1.1：Web，仅简体中文） ----------------
WEB_DIR = os.path.join(ROOT, "web")
if os.path.isdir(WEB_DIR):
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
