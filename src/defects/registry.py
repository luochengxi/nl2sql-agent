"""缺陷开关注册中心（教学用，可切换配置）。

设计原理见 技术方案拆解 §12：同一份代码在 enabled=false 时行为正常，
在 enabled=true 时按 active 列表激活指定缺陷。这样你可以写一组测试，验证
"正常模式全绿 / 缺陷模式应当失败"，从而证明测试真的抓得住这类问题。

注意：CATALOG 只登记【缺陷分类与提示】，不登记位置——位置写在
docs/缺陷答案key.local.md（已 .gitignore，不进公开仓库）。
"""
from __future__ import annotations

from ..core.config import settings

# id -> 分类 / 提示（提示只描述现象，不暴露代码位置）
CATALOG: dict[str, dict[str, str]] = {
    "D01": {"category": "时间口径", "hint": "滚动窗口边界（近 N 日是否含当天）"},
    "D02": {"category": "时间口径", "hint": "环比/同比的对比周期对齐"},
    "D03": {"category": "行级权限", "hint": "组织树下推（角色可见范围）"},
    "D04": {"category": "行级权限", "hint": "下钻时的范围收窄"},
    "D05": {"category": "列级权限", "hint": "敏感字段脱敏表达式"},
    "D06": {"category": "列级权限", "hint": "禁用字段的剔除"},
    "D07": {"category": "只读安全", "hint": "多语句 / 注释绕过"},
    "D08": {"category": "只读安全", "hint": "DDL / DML 关键字拦截"},
    "D09": {"category": "语义层", "hint": "客单价分母口径"},
    "D10": {"category": "语义层", "hint": "有效订单量的状态过滤"},
    "D11": {"category": "意图澄清", "hint": "同名不同口径的歧义处理"},
    "D12": {"category": "意图澄清", "hint": "缺失必要槽位的处理"},
    "D13": {"category": "多轮记忆", "hint": "追问时的槽位继承"},
    "D14": {"category": "多轮记忆", "hint": "会话之间的隔离"},
    "D15": {"category": "口径 RAG", "hint": "口径文档变更后的索引同步"},
    "D16": {"category": "数据导出", "hint": "导出是否复用同一套权限"},
    "D17": {"category": "低置信兜底", "hint": "置信度阈值的执行"},
    "D18": {"category": "审计日志", "hint": "操作留痕的完整性"},
}


class DefectRegistry:
    def __init__(self, cfg: dict) -> None:
        self.enabled: bool = bool(cfg.get("enabled", False))
        raw = cfg.get("active") or []
        self.active: set[str] = {str(x).upper() for x in raw}
        self.activate_all: bool = "ALL" in self.active

    def on(self, defect_id: str) -> bool:
        if not self.enabled:
            return False
        return self.activate_all or defect_id.upper() in self.active

    def active_ids(self) -> list[str]:
        if not self.enabled:
            return []
        return sorted(CATALOG) if self.activate_all else sorted(self.active)

    def catalog(self) -> dict[str, dict[str, str]]:
        return CATALOG


_registry: DefectRegistry | None = None


def get_registry() -> DefectRegistry:
    global _registry
    if _registry is None:
        _registry = DefectRegistry(settings().get("defect_mode", {}))
    return _registry


def on(defect_id: str) -> bool:
    """代码里的唯一判断入口：if defects.on("D03"): ..."""
    return get_registry().on(defect_id)


class _Proxy:
    def on(self, defect_id: str) -> bool:
        return on(defect_id)

    def active_ids(self) -> list[str]:
        return get_registry().active_ids()


defects = _Proxy()
