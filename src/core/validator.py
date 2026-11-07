"""只读校验：执行前的最后一道独立防线（技术方案拆解 §6 的 L3）。

为什么编译器已经只生成 SELECT，还要再校验一次：
纵深防御。LLM 可能被提示词注入绕过，编译器本身可能有 Bug，
所以必须有一道不依赖前两层的、真正贴着数据库连接做的检查。
"""
from __future__ import annotations

import re

from .config import business_lines
from ..defects import registry as _defects


class SecurityError(Exception):
    pass


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|"
    r"ATTACH|DETACH|PRAGMA|VACUUM|EXEC|EXECUTE|REINDEX|LOAD_EXTENSION)\b",
    re.I,
)
_COMMENT = re.compile(r"(--|/\*|\*/|#)")
_VIEW_WHITELIST = set()


def _allowed_views() -> set[str]:
    global _VIEW_WHITELIST
    if not _VIEW_WHITELIST:
        _VIEW_WHITELIST = {
            v["view"] for v in business_lines().get("business_lines", {}).values()
        }
    return _VIEW_WHITELIST


def validate(sql: str, params: list | None = None) -> None:
    s = sql.strip().rstrip(";").strip()

    # 只允许单条 SELECT；编译器不会生成 WITH / 子查询，直接禁止更稳妥
    if not re.match(r"^SELECT\b", s, re.I):
        raise SecurityError("只允许单条 SELECT 查询")

    # [DEFECT-ID: D08] DDL/DML 关键字拦截被跳过
    if not _defects.on("D08"):
        if _FORBIDDEN.search(s):
            raise SecurityError("检测到写操作关键字，已拦截")

    # [DEFECT-ID: D07] 注释 / 多语句绕过检查被跳过
    if not _defects.on("D07"):
        if ";" in s:
            raise SecurityError("检测到多语句，已拦截")
        if _COMMENT.search(s):
            raise SecurityError("检测到 SQL 注释，已拦截")

    # 只允许查询白名单内的宽表视图（不采信任何来自用户的表名）
    views = {v.lower() for v in _allowed_views()}
    if re.search(r"\bFROM\s*\(", s, re.I):
        raise SecurityError("不允许子查询")
    refs = re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", s, re.I)
    for ref in refs:
        if ref.lower() not in views:
            raise SecurityError(f"引用了非白名单表: {ref}")

    # 参数值里不允许出现 SQL 控制字符（参数化之外的兜底）
    for p in params or []:
        if isinstance(p, str) and re.search(r"['\";]|--|/\*", p):
            raise SecurityError("参数值包含非法字符")
