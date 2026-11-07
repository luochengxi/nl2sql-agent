"""配置加载层。

为什么集中在这里（而不是各模块自己 open yaml）:
1. 单一数据源：所有配置只读一次并缓存，避免各处读到不同快照。
2. 可测试：测试里改一份临时 yaml 就能整体替换，不用到处 mock open()。
3. 路径与运行环境解耦：ROOT 由文件位置推导，从任何目录启动都找得到。
"""
from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
from typing import Any

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.path.join(ROOT, "config")
KNOWLEDGE_DIR = os.path.join(ROOT, "knowledge")


def _load(name: str) -> dict[str, Any]:
    with open(os.path.join(CONFIG_DIR, name), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=None)
def settings() -> dict[str, Any]:
    cfg = _load("settings.yaml")
    # 环境变量覆盖缺陷模式：CI / 测试里不必改动配置文件即可逐条激活
    # 用法: NL2SQL_DEFECT_MODE=D03 python ...  或  NL2SQL_DEFECT_MODE=ALL
    override = os.environ.get("NL2SQL_DEFECT_MODE", "").strip()
    if override:
        active = ["ALL"] if override.upper() == "ALL" else [x.strip().upper() for x in override.split(",") if x.strip()]
        cfg["defect_mode"] = {"enabled": True, "active": active}
    return cfg


@lru_cache(maxsize=None)
def rbac() -> dict[str, Any]:
    return _load("rbac.yaml")


@lru_cache(maxsize=None)
def semantic() -> dict[str, Any]:
    return _load("semantic_layer.yaml")


@lru_cache(maxsize=None)
def business_lines() -> dict[str, Any]:
    return _load("business_lines.yaml")


@lru_cache(maxsize=None)
def org_tree() -> dict[str, Any]:
    return _load("org_tree.yaml")


def db_path() -> str:
    return os.path.join(ROOT, settings()["warehouse"]["path"])


def caliber_dir() -> str:
    return os.path.join(KNOWLEDGE_DIR, "caliber")


def today() -> date:
    """统计基准日 T。

    为什么要可注入：依赖真实日期的测试是脆弱测试——今天能过的断言明天就挂。
    钉死 T 之后，时间窗口相关的断言才稳定可复现。
    """
    fixed = settings().get("time", {}).get("today")
    return date.fromisoformat(fixed) if fixed else date.today()


def audit_path() -> str:
    return os.path.join(ROOT, settings().get("audit", {}).get("path", "logs/audit.jsonl"))
