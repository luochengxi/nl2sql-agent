"""下钻明细（F10）：从汇总行展开到明细行。

v1.1 决策：只做"表格态下钻"，不做图表联动下钻。
明细列必须经过与汇总查询完全相同的权限处理：
visible_columns 剔除禁用列 + mask_expression 脱敏 + row_filter_clause 行级下推。
"""
from __future__ import annotations

from .config import business_lines
from .models import QueryPlan, UserContext
from .permission import mask_expression, row_filter_clause, visible_columns
from ..defects import registry as _defects

_DETAIL_CACHE: dict[str, dict] = {}


def create_drilldown_token(plan: QueryPlan, user: UserContext) -> str:
    import uuid
    token = uuid.uuid4().hex[:16]
    _DETAIL_CACHE[token] = {"plan": plan.model_dump(), "user_id": user.user_id}
    return token


def build_detail_sql(token: str, user: UserContext,
                     row_filters: dict[str, str] | None = None,
                     limit: int = 200) -> tuple[str, list]:
    rec = _DETAIL_CACHE.get(token)
    if not rec:
        raise KeyError("下钻凭证不存在或已过期")
    if rec["user_id"] != user.user_id:
        raise PermissionError("无权查看该明细")
    plan = QueryPlan(**rec["plan"])
    bl_cfg = business_lines()["business_lines"][plan.business_line]
    view, date_col = bl_cfg["view"], bl_cfg["date_column"]
    cols = visible_columns(user, business_lines().get("detail_columns", {}).get(plan.business_line, []))
    masks = mask_expression(user)

    select = [f"{masks.get(c, c)} AS \"{c}\"" for c in cols]
    tr = plan.time_range
    where = [f"{date_col} BETWEEN ? AND ?"]
    params: list = [tr.start, tr.end]

    scope_sql, scope_params = row_filter_clause(user)
    if scope_sql != "1=1":
        where.append(scope_sql)
        params.extend(scope_params)
    for f in plan.filters:
        where.append(f"{f.column} = ?")
        params.append(f.value)

    # [DEFECT-ID: D04] 下钻未附加"所点击行"的收窄条件，展开成全量明细
    if not _defects.on("D04"):
        allowed = set(cols)
        for col, val in (row_filters or {}).items():
            if col in allowed:
                where.append(f"{col} = ?")
                params.append(val)

    sql = (
        f"SELECT {', '.join(select)}\nFROM {view}\n"
        f"WHERE {' AND '.join(where)}\n"
        f"ORDER BY {date_col} DESC LIMIT ?"
    )
    params.append(limit)
    return sql, params
