"""QueryPlan -> SQL 编译器（确定性的，不经过 LLM）。

这是全项目唯一生成 SQL 的地方，因此也是：
- 行级权限下推的唯一注入点（permission.row_filter_clause）
- 只读约束的结构性保证（只拼 SELECT 模板）
- 字段映射的唯一来源（语义层 YAML）

用户输入的任何字符串都不会被拼进 SQL 文本，只作为参数值进入 params，
从结构上杜绝注入（见技术方案拆解 §0 / §6）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import business_lines, semantic
from .models import QueryPlan, UserContext
from .permission import check_metric_allowed, row_filter_clause
from ..defects import registry as _defects


@dataclass
class CompiledQuery:
    sql: str
    params: list = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    metric_ids: list[str] = field(default_factory=list)
    dim_ids: list[str] = field(default_factory=list)
    view: str = ""
    date_column: str = ""
    scope_clause: str = ""
    explanation: str = ""


def _metric_expr(metric_id: str) -> str:
    cfg = semantic()["metrics"][metric_id]
    expr = cfg["expr"]
    # [DEFECT-ID: D10] 有效订单量漏掉状态过滤，把取消/退款单也算进来
    if _defects.on("D10") and metric_id == "valid_order_cnt":
        expr = "COUNT(*)"
    # [DEFECT-ID: D09] 客单价分母用了总订单量而非有效订单量
    if _defects.on("D09") and metric_id == "aov":
        expr = "ROUND(SUM(pay_amount) / NULLIF(COUNT(*), 0), 2)"
    return expr


def compile(plan: QueryPlan, user: UserContext,
            time_override: tuple[str, str] | None = None) -> CompiledQuery:
    bl_cfg = business_lines()["business_lines"][plan.business_line]
    view = bl_cfg["view"]
    date_col = bl_cfg["date_column"]
    sem = semantic()
    metrics_cfg, dims_cfg = sem["metrics"], sem["dimensions"]

    for mid in plan.metrics:
        check_metric_allowed(user, mid)

    # --- SELECT 维度 ---
    dim_exprs: list[str] = []
    dim_labels: list[str] = []
    for did in plan.dimensions:
        dcfg = dims_cfg.get(did)
        if not dcfg or plan.business_line not in (dcfg.get("applies_to") or []):
            continue
        expr = str(dcfg["expr"]).replace("{date_col}", date_col)
        dim_exprs.append(expr)
        dim_labels.append(str(dcfg["name"]))

    # --- SELECT 指标 ---
    metric_exprs: list[str] = []
    metric_labels: list[str] = []
    for mid in plan.metrics:
        mcfg = metrics_cfg[mid]
        metric_exprs.append(f"{_metric_expr(mid)} AS \"{mcfg['name']}\"")
        metric_labels.append(str(mcfg["name"]))

    select_parts = [f"{e} AS \"{label}\"" for e, label in zip(dim_exprs, dim_labels)] + metric_exprs
    if not select_parts:
        raise ValueError("QueryPlan 没有可执行的选择项")

    # --- WHERE：时间 + 行级权限 + 用户筛选 ---
    tr = plan.time_range
    start, end = time_override or (tr.start, tr.end)
    where = [f"{date_col} BETWEEN ? AND ?"]
    params: list = [start, end]

    scope_sql, scope_params = row_filter_clause(user)
    if scope_sql != "1=1":
        where.append(scope_sql)
        params.extend(scope_params)

    for f in plan.filters:
        # 列名来自配置/白名单，不来自用户输入
        where.append(f"{f.column} = ?")
        params.append(f.value)

    # --- GROUP BY / ORDER BY ---
    group_by = ""
    if dim_exprs:
        group_by = "GROUP BY " + ", ".join(dim_exprs)
    order_by = "ORDER BY " + ", ".join(str(i + 1) for i in range(len(dim_exprs))) if dim_exprs else ""

    sql = (
        f"SELECT {', '.join(select_parts)}\n"
        f"FROM {view}\n"
        f"WHERE {' AND '.join(where)}\n"
        + (f"{group_by}\n" if group_by else "")
        + (f"{order_by}\n" if order_by else "")
    ).strip()

    explanation = (
        f"从 {view} 按 {'、'.join(dim_labels) or '全量'} 分组，"
        f"统计 {'、'.join(metric_labels)}；"
        f"时间过滤 {date_col} ∈ [{start}, {end}]；"
        f"行级权限下推：{scope_sql}"
    )

    return CompiledQuery(
        sql=sql,
        params=params,
        columns=dim_labels + metric_labels,
        metric_ids=list(plan.metrics),
        dim_ids=list(plan.dimensions),
        view=view,
        date_column=date_col,
        scope_clause=scope_sql,
        explanation=explanation,
    )
