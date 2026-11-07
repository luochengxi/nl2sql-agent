"""意图识别 -> QueryPlan 草稿。

原则（见技术方案拆解 §0 / §4）：
- 输出的是结构化 QueryPlan，不是 SQL。SQL 由 nl2sql.py 编译。
- 抽词用确定性规则（最长匹配优先），LLM 只作为可选增强，
  且时间、组织范围、权限一律不由 LLM 决定。
- 指代消解 = 继承上轮槽位 + 用本轮新信息覆盖，而不是把历史原文塞给模型。
"""
from __future__ import annotations

import re

from .config import business_lines, org_tree, semantic, settings
from .llm import get_provider
from .models import Filter, QueryPlan, TimeRange, UserContext
from .time_parser import default_time_range, parse_time
from ..defects import registry as _defects

_FOLLOWUP = re.compile(r"^(那|那|再|改成|换成|同样|换成|那么|另外|以及|还有|呢|这个|那个|上面|刚才)|呢[？?]?$")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _longest_match(text: str, entries: dict) -> list[tuple[str, str, str]]:
    """返回 [(id, 命中的 alias, 剩余文本)]，按 alias 长度降序，避免短词吃掉长词。"""
    pairs: list[tuple[int, str, str]] = []
    for eid, cfg in entries.items():
        for alias in [str(cfg.get("name", "")), *(cfg.get("aliases") or [])]:
            alias = alias.strip()
            if alias:
                pairs.append((len(alias), alias, eid))
    pairs.sort(key=lambda x: -x[0])

    remaining = text
    hits: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for _, alias, eid in pairs:
        if eid in seen:
            continue
        if alias in remaining:
            hits.append((eid, alias, remaining))
            remaining = remaining.replace(alias, "　", 1)
            seen.add(eid)
    return hits


def _detect_business_line(text: str, metric_ids: list[str]) -> str | None:
    cfg = business_lines().get("business_lines", {})
    scores: dict[str, int] = {}
    for bl_id, bl in cfg.items():
        score = sum(1 for kw in bl.get("keywords", []) if kw in text)
        if score:
            scores[bl_id] = score
    # 指标归属也是强信号
    metrics = semantic().get("metrics", {})
    for mid in metric_ids:
        bl = metrics.get(mid, {}).get("business_line")
        if bl:
            scores[bl] = scores.get(bl, 0) + 2
    if not scores:
        return None
    return max(scores, key=lambda k: scores[k])


def _extract_org_filters(text: str) -> list[Filter]:
    tree = org_tree()
    cols = business_lines().get("scope_columns", {})
    out: list[Filter] = []
    for region_name, cities in tree.get("regions", {}).items():
        if region_name in text:
            out.append(Filter(column=cols["region"], op="=", value=region_name))
            break
    for cities in tree.get("regions", {}).values():
        for city in cities:
            if city in text:
                out.append(Filter(column=cols["city"], op="=", value=city))
                break
    # 店铺名，如 "SH1号店"
    m = re.search(r"[A-Z]{2}\d+号店", text)
    if m:
        out.append(Filter(column="store_name", op="=", value=m.group(0)))
    return out


def _clarify_for_metrics(candidates: list[str], metrics: dict) -> str:
    names = [metrics.get(m, {}).get("name", m) for m in candidates]
    return f"「{'/'.join(names)}」在不同业务线下口径不同，你想看哪一个？（例如：零售线销售额 / 绩效线业绩）"


def _llm_refine(plan: QueryPlan, text: str) -> QueryPlan:
    """可选增强：让 LLM 只建议 metric / dimension / business_line，确定性逻辑再校验。"""
    provider = get_provider()
    if not provider.available:
        return plan
    metrics = semantic().get("metrics", {})
    dims = semantic().get("dimensions", {})
    catalog = {
        "metrics": {k: {"name": v.get("name"), "business_line": v.get("business_line")}
                    for k, v in metrics.items()},
        "dimensions": {k: {"name": v.get("name")} for k, v in dims.items()},
        "business_lines": list(business_lines().get("business_lines", {})),
    }
    system = (
        "你是取数意图解析器。只输出 JSON："
        '{"metrics":[id...],"dimensions":[id...],"business_line":id|null}。'
        "id 必须来自下面这份清单，不要发明新 id，不要输出 SQL。"
        f"清单={catalog}"
    )
    result = provider.complete_json(system, text)
    if not result:
        return plan
    valid_m = [m for m in result.get("metrics", []) if m in metrics]
    valid_d = [d for d in result.get("dimensions", []) if d in dims]
    bl = result.get("business_line")
    if valid_m:
        plan.metrics = list(dict.fromkeys(valid_m + plan.metrics))[:3]
    if valid_d:
        plan.dimensions = list(dict.fromkeys(valid_d + plan.dimensions))[:3]
    if bl in business_lines().get("business_lines", {}):
        plan.business_line = bl
    return plan


def build_plan(query: str, user: UserContext, slots: dict | None = None) -> QueryPlan:
    text = _normalize(query)
    cfg_sem = semantic()
    metrics_cfg: dict = cfg_sem.get("metrics", {})
    dims_cfg: dict = cfg_sem.get("dimensions", {})
    intent_cfg = settings().get("intent", {})

    plan = QueryPlan(raw_query=text, confidence=0.95)
    inherited_slots = slots or {}

    # --- 1. 指标抽取（最长匹配优先） ---
    metric_hits = _longest_match(text, metrics_cfg)
    matched_ids = [mid for mid, _, _ in metric_hits]

    # 判断"同名不同口径"歧义：同一 alias 落在多个业务线
    ambiguous: list[str] = []
    for mid in matched_ids:
        alias_bl = metrics_cfg[mid].get("business_line")
        for other, ocfg in metrics_cfg.items():
            if other == mid:
                continue
            shared = set(metrics_cfg[mid].get("aliases", [])) & set(ocfg.get("aliases", []))
            if shared and ocfg.get("business_line") != alias_bl:
                ambiguous.append(mid)
                break

    # --- 2. 业务线判定 ---
    bl = _detect_business_line(text, matched_ids) or inherited_slots.get("business_line") or "retail"
    plan.business_line = bl

    # 业务线能消除歧义时就自动消歧；否则保留候选等待澄清
    # [DEFECT-ID: D11] 同名不同口径不澄清，直接选第一个候选
    skip_ambiguity_check = _defects.on("D11")
    resolved: list[str] = []
    for mid in matched_ids:
        if mid in ambiguous and not skip_ambiguity_check:
            if intent_cfg.get("clarify_on_ambiguous_metric", True) and not _detect_business_line(text, []):
                plan.metric_candidates.append(mid)
                continue
        resolved.append(mid)
    plan.metrics = list(dict.fromkeys(resolved))

    # --- 3. 维度抽取 ---
    dim_hits = _longest_match(text, dims_cfg)
    # 区分"用户明确指定了维度"与"用默认维度兜底"：追问时必须保留用户的新指定
    dim_specified = bool(dim_hits)
    plan.dimensions = [d for d, _, _ in dim_hits
                       if bl in (dims_cfg[d].get("applies_to") or [])]
    if not plan.dimensions:
        plan.dimensions = [cfg_sem.get("default_view", {}).get(bl, {}).get("dimension", "date")]

    # --- 4. 时间 ---
    tr = parse_time(text)
    if tr is None:
        if inherited_slots.get("time_range"):
            tr = TimeRange(**inherited_slots["time_range"])
        else:
            # [DEFECT-ID: D12] 缺失时间槽位时用全量区间，而不是默认窗口
            tr = (TimeRange(start="2000-01-01", end="2099-12-31", preset="全量")
                  if _defects.on("D12") else default_time_range())
            plan.confidence -= 0.05
    plan.time_range = tr

    # --- 5. 组织范围过滤 ---
    filters = _extract_org_filters(text)
    if not filters and inherited_slots.get("filters"):
        filters = [Filter(**f) for f in inherited_slots["filters"]]
    plan.filters = filters

    # --- 6. 指代消解：追问时继承上轮槽位 ---
    is_followup = bool(_FOLLOWUP.search(text)) or (len(text) <= 12 and not metric_hits)
    if is_followup and inherited_slots:
        # [DEFECT-ID: D13] 追问时槽位继承失效，上轮筛选/指标丢失
        if not _defects.on("D13"):
            if not plan.metrics and inherited_slots.get("metrics"):
                plan.metrics = list(inherited_slots["metrics"])
            if not dim_specified and inherited_slots.get("dimensions"):
                plan.dimensions = list(inherited_slots["dimensions"])
            if not _detect_business_line(text, []) and inherited_slots.get("business_line"):
                plan.business_line = inherited_slots["business_line"]
            plan.inherited = True

    # --- 7. LLM 可选增强（只建议，不覆盖时间与范围） ---
    plan = _llm_refine(plan, text)

    # --- 8. 置信度 / 澄清 ---
    # 歧义导致的"暂无指标"是待澄清，不是识别失败，不重复扣置信度
    if not plan.metrics and not plan.metric_candidates:
        plan.confidence -= 0.40
    if plan.metric_candidates:
        plan.confidence -= 0.30
    if plan.inherited:
        plan.confidence -= 0.03
    plan.confidence = max(0.0, min(1.0, round(plan.confidence, 2)))

    if plan.metric_candidates:
        plan.missing.append("metric")
        plan.clarification_question = _clarify_for_metrics(plan.metric_candidates, metrics_cfg)
    elif not plan.metrics:
        plan.missing.append("metric")
        plan.clarification_question = (
            "没识别出你想看的指标。可以试试：有效订单量、销售额、客单价、及时妥投率、目标达成率？"
        )

    # --- 9. 解释：把"问题 -> 计划"的映射讲清楚 ---
    mnames = [metrics_cfg.get(m, {}).get("name", m) for m in plan.metrics]
    dnames = [dims_cfg.get(d, {}).get("name", d) for d in plan.dimensions]
    plan.explanation = (
        f"识别到：业务线={business_lines()['business_lines'][plan.business_line]['label']}；"
        f"指标={'/'.join(mnames) or '未识别'}；维度={'/'.join(dnames)}；"
        f"时间={tr.preset or '自定义'}（{tr.start} ~ {tr.end}）"
        + (f"；范围={'、'.join(f.value for f in plan.filters)}" if plan.filters else "")
        + ("；已沿用上轮上下文" if plan.inherited else "")
    )
    return plan


def slots_from_plan(plan: QueryPlan) -> dict:
    """把本轮槽位写回会话记忆，供下一轮指代消解。"""
    return {
        "business_line": plan.business_line,
        "metrics": plan.metrics,
        "dimensions": plan.dimensions,
        "time_range": plan.time_range.model_dump() if plan.time_range else None,
        "filters": [f.model_dump() for f in plan.filters],
    }
