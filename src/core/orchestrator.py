"""编排层：取数主流程状态机（唯一的"总指挥"）。

流程（技术方案拆解 §13）：
  问句 -> 权限画像 -> 短期记忆(指代消解) -> 意图识别 -> 澄清?
       -> 口径 RAG -> 业务线/数据集 -> SQL 编译(权限下推)
       -> 只读校验 -> 执行 -> 呈现 -> 导出/下钻/反馈
       -> 写回记忆 + 审计 + 指标

每个环节都是独立模块，这里只负责"按什么顺序调用、什么条件下终止"。
"""
from __future__ import annotations

from .config import business_lines, semantic, settings
from .drilldown import create_drilldown_token
from .executor import QueryResult, run
from .intent import build_plan, slots_from_plan
from ..memory.long_term import record_query
from ..memory.session_store import Turn, get_store
from .models import ChatRequest, ChatResponse, QueryPlan
from .nl2sql import compile
from .permission import PermissionDenied, load_user, scope_label
from .rag import get_index
from .time_parser import describe
from ..defects.registry import get_registry
from ..export.exporter import create_export_token
from ..observability import audit, metrics

# F17 结果缓存：同 SQL + 同参数直接复用。缺陷模式下禁用，避免缓存掩盖缺陷。
_RESULT_CACHE: dict[tuple, QueryResult] = {}


def _cached_run(compiled) -> QueryResult:
    if get_registry().active_ids():
        return run(compiled)
    key = (compiled.sql, tuple(str(p) for p in compiled.params))
    hit = _RESULT_CACHE.get(key)
    if hit is not None:
        return hit
    result = run(compiled)
    if len(_RESULT_CACHE) > 200:
        _RESULT_CACHE.clear()
    _RESULT_CACHE[key] = result
    return result


def _merge_compare(result: QueryResult, compare_result: QueryResult,
                   plan: QueryPlan) -> QueryResult:
    """F9：把对比期结果按维度对齐，追加增长率列。"""
    ndim = len(plan.dimensions)
    if ndim == 0:
        return result
    metric_count = len(plan.metrics)
    label = "同比增长率" if plan.time_range.compare_type == "yoy" else "环比增长率"
    cmap = {tuple(str(r[i]) for i in range(ndim)): r for r in compare_result.rows}
    # 按日期分组时，本期与对比期的维度值（日期）天然不同，
    # 若行数一致则改用"位置对齐"（本期第 i 天 vs 对比期第 i 天）。
    positional = ("date" in plan.dimensions
                  and len(result.rows) == len(compare_result.rows))
    extra_cols = [f"{result.columns[ndim + j]}{label}" for j in range(metric_count)]

    new_rows = []
    for idx, r in enumerate(result.rows):
        key = tuple(str(r[i]) for i in range(ndim))
        prev = compare_result.rows[idx] if positional else cmap.get(key)
        extra = []
        for j in range(metric_count):
            cur_v, prev_v = r[ndim + j], (prev[ndim + j] if prev else None)
            if (isinstance(cur_v, (int, float)) and isinstance(prev_v, (int, float))
                    and prev_v not in (0, None)):
                extra.append(round((cur_v - prev_v) / prev_v, 4))
            else:
                extra.append(None)
        new_rows.append(list(r) + extra)
    result.columns = list(result.columns) + extra_cols
    result.rows = new_rows
    return result


def _chart_spec(plan: QueryPlan, result: QueryResult) -> dict | None:
    ndim = len(plan.dimensions)
    if ndim == 0 or not result.rows:
        return None
    metric_count = len(plan.metrics)
    series = [
        {"name": result.columns[ndim + j],
         "data": [r[ndim + j] for r in result.rows]}
        for j in range(metric_count)
    ]
    ctype = "line" if "date" in plan.dimensions else "bar"
    return {
        "type": ctype,
        "x": [str(r[0]) for r in result.rows],
        "series": series,
        "pie_available": len(result.rows) <= 8 and metric_count == 1,
    }


def _summarize(plan: QueryPlan, result: QueryResult, caliber_docs: list, user) -> str:
    ndim = len(plan.dimensions)
    lines: list[str] = []
    if not result.rows:
        lines.append("该条件下没有查到数据。可能是指标口径、时间范围或你的可见范围导致的。")
    else:
        metric_names = result.columns[ndim:]
        lines.append(f"查到 {len(result.rows)} 行，指标：{'、'.join(metric_names)}。")
        preview = result.rows[:3]
        for r in preview:
            dim_part = "/".join(str(v) for v in r[:ndim]) or "合计"
            val_part = "，".join(f"{metric_names[j]}={r[ndim + j]}" for j in range(len(metric_names)))
            lines.append(f"· {dim_part}：{val_part}")
        if len(result.rows) > 3:
            lines.append(f"…… 其余 {len(result.rows) - 3} 行见下方表格。")
    lines.append(f"时间口径：{describe(plan.time_range)}")
    lines.append(f"可见范围：{scope_label(user)}")
    if caliber_docs:
        lines.append("引用口径：" + "；".join(d.ref() for d in caliber_docs))
    return "\n".join(lines)


def _suggestions(plan: QueryPlan) -> list[str]:
    bl_label = business_lines()["business_lines"][plan.business_line]["label"]
    return [
        "按天看趋势",
        "换成销售额 / 客单价对比",
        f"查看{bl_label}明细",
        "导出 CSV / Excel",
    ]


def _clarify_response(session_id: str, plan: QueryPlan, user_id: str) -> ChatResponse:
    metrics.incr("clarify")
    audit.log(user_id, session_id, "clarify", question=plan.raw_query,
              question_to_user=plan.clarification_question)
    return ChatResponse(
        session_id=session_id,
        answer_type="clarify",
        text=plan.clarification_question or "请补充信息",
        confidence=plan.confidence,
        plan=plan.model_dump(),
        explanation=plan.explanation,
        suggestions=["近 7 日各城市有效订单量", "华东本月销售额", "物流及时妥投率环比"],
    )


def _fallback_response(session_id: str, plan: QueryPlan, user_id: str) -> ChatResponse:
    metrics.incr("fallback")
    audit.log(user_id, session_id, "fallback", question=plan.raw_query,
              confidence=plan.confidence)
    return ChatResponse(
        session_id=session_id,
        answer_type="fallback",
        text=("这次没太理解你的问题，为避免给错数据我不做猜测。"
              "可以换个说法，或从下面的示例问题开始。"),
        confidence=plan.confidence,
        plan=plan.model_dump(),
        suggestions=["近 7 日各城市有效订单量", "华东本月销售额环比", "上海各门店客单价"],
    )


def chat(req: ChatRequest) -> ChatResponse:
    metrics.incr("query")
    try:
        user = load_user(req.user_id)
    except PermissionDenied as e:
        return ChatResponse(session_id="", answer_type="error", text=str(e))

    store = get_store()
    session = store.get(req.session_id, req.user_id) if req.session_id else None
    if session is None:
        sid = store.create(req.user_id)
        session = store.get(sid, req.user_id)

    # --- 多轮：澄清追问时把用户回答与上一轮问句合并，重新解析 ---
    effective_query = req.query
    pending = (session.slots or {}).get("pending_query")
    if pending:
        effective_query = f"{req.query} {pending}"

    plan = build_plan(effective_query, user, session.slots)

    # --- 低置信兜底（F12）：置信度极低时优先保守处理，不再反问 ---
    min_conf = float(settings().get("intent", {}).get("min_confidence", 0.60))
    low_conf = plan.confidence < min_conf
    # [DEFECT-ID: D17] 低置信仍强行出数：跳过兜底并补齐默认指标
    if low_conf and not get_registry().on("D17"):
        return _fallback_response(session.session_id, plan, req.user_id)
    if low_conf and get_registry().on("D17"):
        if not plan.metrics:
            plan.metrics = [business_lines()["business_lines"][plan.business_line].get(
                "default_metric", "valid_order_cnt")]
        plan.missing = []
        plan.clarification_question = None

    # --- 澄清：把待确认的原问句留在会话里，下一轮合并重解析 ---
    if plan.clarification_question:
        store.touch(session, slots={**(session.slots or {}), "pending_query": plan.raw_query})
        return _clarify_response(session.session_id, plan, req.user_id)

    # 澄清已解决，清掉 pending
    if pending:
        session.slots.pop("pending_query", None)

    # --- F11 显式业务线切换（前端可指定数据集） ---
    if req.business_line and req.business_line in business_lines()["business_lines"]:
        plan.business_line = req.business_line
        for d in list(plan.dimensions):
            applies = semantic()["dimensions"].get(d, {}).get("applies_to") or []
            if plan.business_line not in applies:
                plan.dimensions.remove(d)
        if not plan.dimensions:
            plan.dimensions = ["date"]

    # --- 口径 RAG（C5） ---
    metric_names = []
    for mid in plan.metrics:
        metric_names.append(settings_metric_name(mid))
    docs = get_index().search(effective_query, plan.business_line, metric_names, top_k=3)
    plan.caliber_refs = [d.ref() for d in docs]

    # --- 编译 + 执行 ---
    try:
        compiled = compile(plan, user)
        result = _cached_run(compiled)
        # --- 周期对比（F9） ---
        tr = plan.time_range
        if tr.compare_type and tr.compare_start and tr.compare_end:
            cmp_compiled = compile(plan, user, (tr.compare_start, tr.compare_end))
            cmp_result = _cached_run(cmp_compiled)
            result = _merge_compare(result, cmp_result, plan)
    except PermissionDenied as e:
        return ChatResponse(session_id=session.session_id, answer_type="error",
                            text=f"权限校验未通过：{e}", plan=plan.model_dump())
    except Exception as e:
        return ChatResponse(session_id=session.session_id, answer_type="error",
                            text=f"取数失败：{type(e).__name__}: {e}", plan=plan.model_dump())

    metrics.observe_latency(result.elapsed_ms)
    metrics.incr("result")

    # --- 出口：导出凭证 / 下钻凭证 ---
    export_token = create_export_token(
        user.user_id, result.columns, result.rows, compiled.sql, plan.caliber_refs
    )
    drilldown_token = create_drilldown_token(plan, user)
    dim_columns = []
    for d in plan.dimensions:
        dcfg = semantic()["dimensions"].get(d, {})
        dim_columns.append(dcfg.get("column") or compiled.date_column)

    text = _summarize(plan, result, docs, user)
    explanation = plan.explanation + "\n" + compiled.explanation
    if tr.compare_type:
        explanation += f"\n对比期 {tr.compare_start} ~ {tr.compare_end}（{'同比' if tr.compare_type == 'yoy' else '环比'}）"

    # --- 写回记忆 / 历史 / 审计 ---
    store.touch(
        session,
        slots=slots_from_plan(plan),
        turn=Turn(query=req.query, answer_type="result", plan=plan.model_dump()),
    )
    record_query(user.user_id, req.query, plan.model_dump(), compiled.sql)
    audit.log(user.user_id, session.session_id, "query",
              question=req.query, sql=compiled.sql, row_count=len(result.rows),
              business_line=plan.business_line, confidence=plan.confidence)

    return ChatResponse(
        session_id=session.session_id,
        answer_type="result",
        text=text,
        sql=compiled.sql,
        columns=result.columns,
        rows=result.rows,
        row_count=len(result.rows),
        chart=_chart_spec(plan, result),
        confidence=plan.confidence,
        plan=plan.model_dump(),
        caliber_refs=plan.caliber_refs,
        explanation=explanation,
        export_token=export_token,
        drilldown_token=drilldown_token,
        dim_columns=dim_columns,
        suggestions=_suggestions(plan),
    )


def settings_metric_name(metric_id: str) -> str:
    from .config import semantic
    return semantic()["metrics"].get(metric_id, {}).get("name", metric_id)



def chat_fc(req: ChatRequest) -> ChatResponse:
    """function calling 版主流程：模型选工具 -> 执行 -> 回填 -> 再决策。"""
    import json
    from .config import today
    from .llm import get_provider
    from .permission import load_user, PermissionDenied, scope_label
    from . import dispatcher, tool_schemas
    from ..observability import trace, metrics

    turn_id = trace.new_turn()
    metrics.incr("query")
    try:
        user = load_user(req.user_id)
    except PermissionDenied as e:
        return ChatResponse(session_id="", answer_type="error", text=str(e),
                            trace_id=turn_id)

    provider = get_provider()
    if not provider.supports_tools:
        return ChatResponse(session_id="", answer_type="error",
                            text="当前 provider 不支持 function calling", trace_id=turn_id)

    system = (
        "你是零售取数 Agent。必须调用工具回答，不要反问用户，不要自己编答案。"
        f"今天是 {today().isoformat()}。用户可见范围：{scope_label(user)}。"
        "用户说'近7日'指含从昨日起在内最近7天：比如今天是T, date_start=T-7，date_end=T-1，"
        "换算成 YYYY-MM-DD 再调用 query_metrics。"
        "问物流时效查 logistics，问绩效查 performance，其余查 retail。"
        "用户说导出/下载就调 export_result，说看明细/展开就调 drilldown_detail，"
        "说查历史就调 query_history。导出和下钻必须先调 query_metrics 拿到结果。"
        "不要编造指标 id。"
    )
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": req.query}]

    last_result = None
    for rnd in range(1, dispatcher.MAX_ROUNDS + 1):
        resp = provider.complete_with_tools(messages, tool_schemas.tools_schema())
        if resp is None:
            break
        calls = resp.get("tool_calls") or []
        trace.decision(turn_id, rnd, calls, resp.get("finish_reason"))
        if not calls:
            break
        messages.append({"role": "assistant", "content": resp.get("content") or "",
                         "tool_calls": [
                             {"id": c.get("id"), "type": "function",
                              "function": {"name": c.get("name"),
                                           "arguments": json.dumps(c.get("arguments") or {},
                                                                   ensure_ascii=False)}}
                             for c in calls]})
        for c in calls:
            out = dispatcher.execute(turn_id, rnd, c, user)
            messages.append({"role": "tool", "tool_call_id": c.get("id"),
                             "content": json.dumps(out, ensure_ascii=False)})
            if c.get("name") == "query_metrics" and out["status"] == "ok":
                last_result = out["result"]
        if rnd == dispatcher.MAX_ROUNDS:
            metrics.incr("tool_round_limit")

    if last_result is None:
        return ChatResponse(
            session_id="", answer_type="fallback",
            text="这次没能确定要查的业务线和指标，请补充（如'零售线近7日各城市有效订单量'）。",
            trace_id=turn_id,
        )
    # 出口：从 turn 上下文取全量结果（dispatcher 给模型的只是 rows[:5] 摘要），
    # 补齐 web 渲染所需字段，使 chat-fc 与旧 chat 的响应结构一致。
    ctx = dispatcher.get_ctx(turn_id)
    full = ctx.get("last_result")
    plan = ctx.get("last_plan")
    if full is None or plan is None:
        return ChatResponse(session_id="", answer_type="fallback",
                            text="取数完成但上下文已丢失，请重试。", trace_id=turn_id)

    export_token = create_export_token(
        user.user_id, full.columns, full.rows, last_result["sql"], plan.caliber_refs
    )
    drilldown_token = create_drilldown_token(plan, user)
    dim_columns = []
    for d in plan.dimensions:
        dcfg = semantic()["dimensions"].get(d, {})
        dim_columns.append(dcfg.get("column") or "date")

    return ChatResponse(
        session_id="", answer_type="result",
        text=_summarize(plan, full, [], user),
        sql=last_result["sql"], columns=full.columns,
        rows=full.rows, row_count=len(full.rows),
        chart=_chart_spec(plan, full),
        confidence=plan.confidence,
        plan=plan.model_dump(),
        caliber_refs=plan.caliber_refs,
        explanation=plan.explanation,
        export_token=export_token,
        drilldown_token=drilldown_token,
        dim_columns=dim_columns,
        suggestions=_suggestions(plan),
        trace_id=turn_id,
    )

