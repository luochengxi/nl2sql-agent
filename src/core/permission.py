"""权限：行级下推 + 列级脱敏 + 权限画像。

核心原则（见技术方案拆解 §5）：隔离必须在 SQL 的 WHERE / SELECT 里生效，
不能"取全量再用 Python 过滤"——聚合指标（SUM/AVG）事后过滤根本不成立，
而且越权数据一旦进了应用内存，日志和异常堆栈就可能把它泄露出去。
"""
from __future__ import annotations

from .config import business_lines, rbac, semantic
from .models import UserContext
from ..defects import registry as _defects


class PermissionDenied(Exception):
    pass


def load_user(user_id: str) -> UserContext:
    users = rbac().get("users", [])
    for u in users:
        if u.get("user_id") == user_id:
            role = u.get("role", "")
            role_cfg = rbac().get("roles", {}).get(role, {})
            policy_name = role_cfg.get("column_policy", "full")
            policy = rbac().get("column_policies", {}).get(policy_name, {"deny": [], "mask": {}})
            return UserContext(
                user_id=user_id,
                name=u.get("name", user_id),
                role=role,
                role_label=role_cfg.get("label", role),
                scope=role_cfg.get("scope", "all"),
                scope_values=u.get("scope_values", {}) or {},
                column_policy={"name": policy_name, **policy},
            )
    raise PermissionDenied(f"未知账号: {user_id}")


def list_users() -> list[dict]:
    out = []
    for u in rbac().get("users", []):
        role_cfg = rbac().get("roles", {}).get(u.get("role", ""), {})
        out.append({
            "user_id": u.get("user_id"),
            "name": u.get("name"),
            "role": u.get("role"),
            "role_label": role_cfg.get("label"),
            "scope_values": u.get("scope_values", {}),
        })
    return out


def scope_columns_map() -> dict[str, str]:
    return business_lines().get("scope_columns", {})


def row_filter_clause(user: UserContext) -> tuple[str, list]:
    """生成行级过滤条件（参数化，杜绝字符串拼接注入）。"""
    cols = scope_columns_map()

    # [DEFECT-ID: D03] 区域经理的组织树下推缺失：可见范围被放大到全量
    if _defects.on("D03") and user.role == "region_manager":
        return "1=1", []

    if user.scope == "all":
        return "1=1", []
    if user.scope == "region":
        return f"{cols['region']} = ?", [user.scope_values.get("region_name")]
    if user.scope == "city":
        return f"{cols['city']} = ?", [user.scope_values.get("city_name")]
    if user.scope == "store":
        return f"{cols['store']} = ?", [user.scope_values.get("store_id")]
    return "1=1", []


def denied_columns(user: UserContext) -> list[str]:
    policy = user.column_policy or {}
    # [DEFECT-ID: D06] 禁用字段清单未被应用
    if _defects.on("D06"):
        return []
    return list(policy.get("deny", []) or [])


def mask_expression(user: UserContext) -> dict[str, str]:
    """列名 -> 脱敏 SQL 表达式。"""
    policy = user.column_policy or {}
    rules = rbac().get("mask_rules", {})
    out: dict[str, str] = {}
    for col, rule in (policy.get("mask") or {}).items():
        tpl = rules.get(rule)
        if tpl:
            out[col] = tpl.replace("{col}", col)
    # [DEFECT-ID: D05] 脱敏表达式未生效：直接返回原列名
    if _defects.on("D05"):
        return {c: c for c in out}
    return out


def check_metric_allowed(user: UserContext, metric_id: str) -> None:
    """指标依赖的字段若被禁用，则该指标不可用。"""
    metric = semantic().get("metrics", {}).get(metric_id)
    if not metric:
        return
    denied = set(denied_columns(user))
    blocked = [c for c in (metric.get("requires") or []) if c in denied]
    if blocked:
        raise PermissionDenied(
            f"指标「{metric.get('name', metric_id)}」依赖的字段 {blocked} 不在你的可见范围内"
        )


def visible_columns(user: UserContext, columns: list[str]) -> list[str]:
    denied = set(denied_columns(user))
    return [c for c in columns if c not in denied]


def scope_label(user: UserContext) -> str:
    if user.scope == "all":
        return "全部数据"
    if user.scope == "region":
        return f"区域：{user.scope_values.get('region_name', '?')}"
    if user.scope == "city":
        return f"城市：{user.scope_values.get('city_name', '?')}"
    if user.scope == "store":
        return f"店铺：{user.scope_values.get('store_id', '?')}"
    return "未知范围"
