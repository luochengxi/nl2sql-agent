"""输出形式匹配：表格 / 图表 / 排序 / 筛选。

表格、图表验收"结构完整"；排序按需求要求生效（当前 agent 未实现 → 红 = 缺陷）。
"""
import pytest
from src.core.config import semantic

WIN = "2026-11-02"


# —— ① 表格：用户要表格就给完整表格 ——
@pytest.mark.parametrize("query,metric", [
    ("2026-11-02 按区域统计有效订单量", "valid_order_cnt"),
    ("2026-11-02 按城市统计销售额",     "pay_amount"),
    ("2026-11-01到2026-11-02 按天统计销量", "sales_qty"),
], ids=["区域", "城市", "按天"])
def test_table_shape(chat, query, metric):
    resp = chat("U001", query)
    assert resp["answer_type"] == "result", resp.get("text")

    cols = resp["columns"]
    nd = len(resp["plan"]["dimensions"])
    nm = len(resp["plan"]["metrics"])
    assert cols, "表格没有列名"
    assert len(cols) == nd + nm, (cols, nd, nm)          # 列 = 维度 + 指标
    assert resp["row_count"] == len(resp["rows"]) > 0, "行数与数据不一致或为空"
    assert all(len(r) == len(cols) for r in resp["rows"]), "存在残缺行"
    assert len(resp["dim_columns"]) == nd, "下钻用物理列名数量不符"
    assert cols[-1] == semantic()["metrics"][metric]["name"], "指标列名不符"


# —— ② 图表：F3 基础图表，有维度自动生成 ——
def test_chart_bar_for_region(chat):
    resp = chat("U001", "2026-11-02 按区域统计有效订单量")
    c = resp["chart"]
    assert c is not None, "按区域分组应自动生成图表"
    assert c["type"] == "bar", c["type"]
    assert len(c["x"]) == resp["row_count"]
    assert len(c["series"]) == 1
    assert c["series"][0]["data"] == [r[-1] for r in resp["rows"]], "图表数据与表格不一致"


def test_chart_line_for_date(chat):
    resp = chat("U001", "2026-11-01到2026-11-02 按天统计有效订单量")
    c = resp["chart"]
    assert c is not None and c["type"] == "line", c
    assert len(c["x"]) == resp["row_count"]


# —— ③ 排序：需求要求"要排序就做到"；当前 agent 忽略排序词 → 红 = 缺陷 ——
@pytest.mark.parametrize("query", [
    "2026-11-02 按区域统计销售额，按销售额降序排列",
    "2026-11-02 按区域统计销售额，销售额从高到低",
], ids=["降序", "从高到低"])
def test_sort_by_metric_desc(chat, query):
    resp = chat("U001", query)
    assert resp["answer_type"] == "result", resp.get("text")
    vals = [r[-1] for r in resp["rows"]]
    assert vals == sorted(vals, reverse=True), (
        f"用户要降序，实际未按指标排序：{vals}\nSQL={resp.get('sql')}")


def test_sort_top_n(chat):
    resp = chat("U001", "2026-11-02 销售额最高的3个区域")
    assert resp["answer_type"] == "result", resp.get("text")
    assert resp["row_count"] == 3, f"用户要 TOP3，实际返回 {resp['row_count']} 行"


# —— ④ 筛选在表格中体现：筛选后维度列只剩筛选值 ——
def test_filter_reflected_in_table(chat):
    resp = chat("U001", "2026-11-02 华东按区域统计有效订单量")
    assert resp["answer_type"] == "result", resp.get("text")
    i = resp["dim_columns"].index("region_name")
    assert {r[i] for r in resp["rows"]} == {"华东"}, f"筛选未体现在输出表格：{resp['rows']}"
