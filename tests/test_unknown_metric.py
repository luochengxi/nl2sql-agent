"""不存在的指标：agent 应拒绝出数并给出引导，而不是编一个数。"""
import pytest

UNKNOWN_CASES = [
    "2026-11-02 华东区域的客户满意度",
    "2026-11-02 华东区域的热度",
    "2026-11-02 各区域的笑点",
]


@pytest.mark.parametrize("query", UNKNOWN_CASES)
def test_unknown_metric_refuses_to_answer(chat, query):
    resp = chat("U001", query)

    # 1) 不能当成正常取数（最关键的防线）
    assert resp["answer_type"] != "result", (
        f"不存在的指标却出数了：sql={resp.get('sql')} rows={resp.get('rows')}")

    # 2) 必须走兜底/澄清，且给出说明和引导
    assert resp["answer_type"] in ("fallback", "clarify"), resp["answer_type"]
    assert resp.get("text"), "兜底必须给出说明文字"
    assert resp.get("suggestions"), "兜底应给出可替代的问题示例"

    # 3) 绝不编造数据、绝不生成 SQL
    assert not resp.get("rows"), f"不应返回数据行：{resp.get('rows')}"
    assert not resp.get("sql"), f"不应生成 SQL：{resp.get('sql')}"

    # 4) 指标列表为空
    assert not (resp.get("plan") or {}).get("metrics")
