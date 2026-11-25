"""工具执行器：模型给指令，这里做校验、执行、留痕、出错兜底。

为什么不抛异常：模型填错参数、查询报错都是"无效调用"，
要变成一条 status=error 的 trace 让测试能断言，而不是被异常吞掉。
"""
from __future__ import annotations

import time

from . import tool_schemas
from .executor import run
from .nl2sql import compile
from .rag import get_index
from .time_parser import compare_window
from ..observability import trace

MAX_ROUNDS = 3          # 模型最多决策轮数，超过判超限

# turn 级上下文：query_metrics 成功后写入，供后置工具（导出/下钻）读取。
# 这就是"有上下文依赖"的状态载体——按 turn_id 隔离，不跨请求串数据。
_TURN_CTX: dict[str, dict] = {}


def reset_ctx(turn_id: str | None = None) -> None:
    """清理上下文，测试隔离用。"""
    if turn_id:
        _TURN_CTX.pop(turn_id, None)
    else:
        _TURN_CTX.clear()


def get_ctx(turn_id: str | None = None) -> dict:
    """读取 turn 上下文快照（编排层补齐响应字段用）。

    返回副本，避免调用方改动污染工具链状态。
    """
    if not turn_id:
        return {}
    return dict(_TURN_CTX.get(turn_id, {}))


def _handlers() -> dict:
    """工具名 -> 执行函数。集中在这里，注册新工具只改这一处。"""
    from .config import business_lines, semantic
    from .models import QueryPlan, TimeRange

    def query_metrics(a: dict, u, ctx: dict) -> dict:
        plan = QueryPlan(
            business_line=a["business_line"],
            metrics=a["metrics"],
            dimensions=a.get("dimensions", []),
            time_range=TimeRange(start=a["date_start"], end=a["date_end"]),
        )
        compiled = compile(plan, u)
        result = run(compiled)
        payload = {
            "row_count": len(result.rows),
            "columns": result.columns[:10],
            "rows": result.rows[:5],          # 摘要，不塞全量
            "sql": compiled.sql,
            "view": compiled.view,
        }
        # 只有取数成功才写上下文，失败/空结果不写，避免后置工具拿到脏数据
        if result.rows:
            ctx["last_result"] = result          # 全量结果，供导出用
            ctx["last_plan"] = plan              # 计划，供下钻用
            ctx["last_meta"] = payload
        return payload

    def search_caliber(a: dict, u, ctx: dict) -> dict:
        docs = get_index().search(a["query"], a.get("business_line"), top_k=3)
        return {
            "doc_count": len(docs),
            "docs": [d.ref() for d in docs],
        }

    def compare_period(a: dict, u, ctx: dict) -> dict:
        """同比/环比：复用本期 plan 查对比期，按维度对齐输出当前/对比/差值/率值。

        依赖 query_metrics 的 ctx 快照（last_plan + last_result），因此模型
        必须先调 query_metrics。对比结果单独存 ctx["compare_result"]，
        不覆盖 last_result——避免 r2 的额外 query_metrics 冲掉本期结果（HP-019）。
        """
        plan = ctx.get("last_plan")
        current = ctx.get("last_result")
        if plan is None or current is None or plan.time_range is None:
            raise ValueError("没有可对比的本期结果，请先调用 query_metrics")
        if plan.business_line != a["business_line"]:
            raise ValueError(f"业务线不一致: 本期 {plan.business_line} vs 对比 {a['business_line']}")

        window = compare_window(plan.time_range.start, plan.time_range.end,
                                a["compare_type"])
        if window is None:
            raise ValueError(f"不支持的对比类型: {a['compare_type']}")
        cs, ce = window

        # 同一 plan，只换时间窗：权限、指标、维度、口径全沿用
        cmp_compiled = compile(plan, u, (cs, ce))
        cmp_result = run(cmp_compiled)

        ndim = len(plan.dimensions)
        nmetric = len(plan.metrics)
        # 对齐策略同编排层 _merge_compare：日期按位置，其余按维度 key
        cmap = {tuple(str(r[i]) for i in range(ndim)): r for r in cmp_result.rows}
        positional = ("date" in plan.dimensions
                      and len(current.rows) == len(cmp_result.rows))

        rows = []
        for idx, r in enumerate(current.rows):
            key = tuple(str(r[i]) for i in range(ndim))
            prev = cmp_result.rows[idx] if positional else cmap.get(key)
            metrics = []
            for j in range(nmetric):
                cur_v = r[ndim + j]
                prev_v = prev[ndim + j] if prev is not None else None
                diff = rate = None
                if isinstance(cur_v, (int, float)) and isinstance(prev_v, (int, float)):
                    diff = round(cur_v - prev_v, 4)
                    if prev_v != 0:
                        rate = round(diff / prev_v, 4)
                metrics.append({
                    "metric": plan.metrics[j],
                    "current": cur_v,
                    "previous": prev_v,
                    "diff": diff,
                    "rate": rate,
                })
            rows.append({"dim": list(key), "metrics": metrics})

        payload = {
            "compare_type": a["compare_type"],
            "business_line": a["business_line"],
            "compare_window": {"start": cs, "end": ce},
            "dimensions": list(plan.dimensions),
            "columns": list(current.columns),
            "rows": rows,
            "sql": cmp_compiled.sql,
            "note": "当前值/对比值按维度对齐；增长率=(当前-对比)/对比，对比期为0时为空",
        }
        # 全量存 turn 上下文，编排层出口据此组装响应；不写 last_result
        ctx["compare_result"] = payload
        return {
            "compare_type": a["compare_type"],
            "compare_window": payload["compare_window"],
            "row_count": len(rows),
            "note": "已算出当前值/对比值/差值/增长率，写在响应的 compare 字段",
        }

    def get_data_dictionary(a: dict, u, ctx: dict) -> dict:
        cfg = semantic()
        return {
            "business_lines": list(business_lines()["business_lines"]),
            "metrics": list(cfg["metrics"]),
            "dimensions": list(cfg["dimensions"]),
        }

    def export_result(a: dict, u, ctx: dict) -> dict:
        from ..export.exporter import create_export_token
        last = ctx.get("last_result")
        if last is None:
            raise ValueError("没有可导出的结果，请先调用 query_metrics")
        token = create_export_token(
            u.user_id, last.columns, last.rows, ctx["last_meta"]["sql"], []
        )
        return {"token": token, "fmt": a.get("fmt", "csv"),
                "row_count": len(last.rows)}

    def drilldown_detail(a: dict, u, ctx: dict) -> dict:
        from .drilldown import create_drilldown_token
        plan = ctx.get("last_plan")
        if plan is None:
            raise ValueError("没有可下钻的结果，请先调用 query_metrics")
        token = create_drilldown_token(plan, u)
        return {"token": token, "note": "明细请用 /api/drilldown 凭证查看"}

    def query_history(a: dict, u, ctx: dict) -> dict:
        from ..memory import long_term
        rows = long_term.history(u.user_id, a.get("limit", 5))
        return {"history_count": len(rows),
                "recent": [r["query"] for r in rows[:5]]}

    return {
        "query_metrics": query_metrics,
        "search_caliber": search_caliber,
        "compare_period": compare_period,
        "get_data_dictionary": get_data_dictionary,
        "export_result": export_result,
        "drilldown_detail": drilldown_detail,
        "query_history": query_history,
    }


def execute(turn_id: str, round_no: int, call: dict, user) -> dict:
    """校验 + 执行 + 打点，返回给编排层回填给模型。

    返回结构固定：{"status": ok|empty|error, "error": 原因或None, "result": 摘要}
    """
    t0 = time.perf_counter()
    name = call.get("name")
    args = call.get("arguments") or {}

    err = tool_schemas.validate_call(name, args)
    status = "ok"
    result: dict = {}

    if err is None:
        try:
            handler = _handlers().get(name)
            if handler is None:
                raise KeyError(f"unknown_tool:{name}")
            ctx = _TURN_CTX.setdefault(turn_id, {})
            result = handler(args, user, ctx)
            # 空结果也算一种"无效调用"，单独标记，便于测"调用后返回空"
            if result.get("row_count") == 0 or result.get("doc_count") == 0:
                status = "empty"
        except Exception as e:
            status = "error"
            err = f"{type(e).__name__}: {e}"
    else:
        status = "error"

    trace.tool_call(
        turn_id, round_no, call.get("id"), name, args, result,
        status, (time.perf_counter() - t0) * 1000, err,
    )
    return {"status": status, "error": err, "result": result}
