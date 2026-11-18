"""指标语义匹配测试：近义别名是否准确映射到同一指标，歧义词是否正确澄清。

用例从 semantic_layer.yaml 自动生成——新增别名/指标 = 改配置，测试自动覆盖，零代码改动。
"""
import pytest
from src.core.config import semantic

METRICS = semantic()["metrics"]



def _aliases(cfg):
    """一个指标的 name + aliases，去重。"""
    raw = [(cfg.get("name") or "").strip(),
           *[str(a).strip() for a in (cfg.get("aliases") or [])]]
    return list(dict.fromkeys(a for a in raw if a))


def _ambiguous():
    """跨业务线共享的词（GMV / 销售额）会触发澄清，不能当作'应命中某指标'的用例。"""
    where = {}
    for mid, cfg in METRICS.items():
        for a in _aliases(cfg):
            where.setdefault(a, set()).add(cfg.get("business_line"))
    return {a for a, bls in where.items() if len(bls) > 1}


AMBIGUOUS = _ambiguous()

# (别名, 期望 metric id)：排除歧义词
POSITIVE = [(a, mid) for mid, cfg in METRICS.items()
            for a in _aliases(cfg) if a not in AMBIGUOUS]


@pytest.mark.parametrize("alias,expected", POSITIVE, ids=[a for a, _ in POSITIVE])
def test_synonym_maps_to_metric(chat, alias, expected):
    """同一指标的每个别名/近义词，都应识别为该指标。"""
    resp = chat("U001", f"2026-11-02 华东区域{alias}")
    assert resp["answer_type"] == "result", (
        f"[{alias}] 未出数：type={resp['answer_type']} text={resp.get('text')}")
    assert resp["plan"]["metrics"] == [expected], (
        f"[{alias}] 语义匹配错误：期望 [{expected}]，实际 {resp['plan']['metrics']}")

@pytest.mark.parametrize("alias", sorted(AMBIGUOUS), ids=lambda a: f"歧义:{a}")
def test_ambiguous_alias_triggers_clarify(chat, alias):
    """跨业务线同名指标必须澄清，不能私自选一个业务线。"""
    resp = chat("U001", f"2026-11-02 {alias}")
    assert resp["answer_type"] == "clarify", (
        f"[{alias}] 应澄清却 type={resp['answer_type']}，metrics={resp['plan'].get('metrics')}")
    assert resp["plan"]["metric_candidates"], f"[{alias}] 澄清未给出候选指标"


@pytest.mark.parametrize("word,expected,forbidden", [
    ("有效订单量", "valid_order_cnt", "order_cnt"),   # 有效订单 ≠ 全部订单
    ("订单量",     "order_cnt", "valid_order_cnt"),   # 反向也要成立
    ("客单价",     "aov", "pay_amount"),              # 均值 ≠ 总额
    ("成交额",     "pay_amount", "aov"),
    ("发货单量",   "shipment_cnt", "order_cnt"),      # 物流线 ≠ 零售线
])
def test_similar_metrics_not_confused(chat, word, expected, forbidden):
    """易混指标对：问 A 必须命中 A，绝不能串成 B。"""
    resp = chat("U001", f"2026-11-02 华东区域{word}")
    assert resp["answer_type"] == "result"
    m = resp["plan"]["metrics"]
    assert expected in m, f"[{word}] 期望 {expected}，实际 {m}"
    assert forbidden not in m, f"[{word}] 误命中易混指标 {forbidden}"