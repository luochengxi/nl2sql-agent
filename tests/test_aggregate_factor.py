"""聚合因子测试：累计(SUM) / 平均(AVG) / 最大(MAX) / 最小(MIN)。

设计要点：
1. 真值用【独立 SQL】直接打物理宽表算，不读 semantic_layer 的 expr —— 断开同源，
   这样"expr 本身写错"也能被发现。
2. 四种聚合用同一断言：结果数值 == 独立真值，差别只在真值 SQL 的聚合函数。
3. 当前 agent 未实现 MAX/MIN（忽略聚合词、按指标自带 SUM/AVG 出数），
   相关用例会红 —— 这个红是 agent 的能力缺陷证据，不是测试设计错误。
"""
import sqlite3
import pytest
from src.core.config import db_path

WIN = ("2026-11-02", "2026-11-02")
REGION = "华东"

# (聚合词, 指标别名, 独立真值SQL, 期望metric id)
AGG_CASES = [
    # —— 累计 SUM ——
    ("累计", "成交额",
     "SELECT ROUND(SUM(pay_amount),2) FROM v_retail_order "
     "WHERE order_date BETWEEN ? AND ? AND region_name=?", "pay_amount"),
    ("累计", "销量",
     "SELECT SUM(qty) FROM v_retail_order "
     "WHERE order_date BETWEEN ? AND ? AND region_name=?", "sales_qty"),
    ("累计", "佣金",
     "SELECT ROUND(SUM(commission),2) FROM v_performance "
     "WHERE stat_date BETWEEN ? AND ? AND region_name=?", "commission"),
    # —— 平均 AVG ——
    ("平均", "平均履约时效",
     "SELECT ROUND(AVG(delivery_hours),1) FROM v_logistics_ship "
     "WHERE ship_date BETWEEN ? AND ? AND region_name=?", "avg_delivery_hours"),
    ("平均", "客单价",
     "SELECT ROUND(SUM(pay_amount)/NULLIF(SUM(CASE WHEN is_valid=1 THEN 1 ELSE 0 END),0),2) "
     "FROM v_retail_order WHERE order_date BETWEEN ? AND ? AND region_name=?", "aov"),
    # —— 最大 MAX ——
    ("最大", "成交额",
     "SELECT ROUND(MAX(pay_amount),2) FROM v_retail_order "
     "WHERE order_date BETWEEN ? AND ? AND region_name=?", "pay_amount"),
    ("最大", "销量",
     "SELECT MAX(qty) FROM v_retail_order "
     "WHERE order_date BETWEEN ? AND ? AND region_name=?", "sales_qty"),
    ("最大", "履约时效",
     "SELECT ROUND(MAX(delivery_hours),1) FROM v_logistics_ship "
     "WHERE ship_date BETWEEN ? AND ? AND region_name=?", "avg_delivery_hours"),
    # —— 最小 MIN ——
    ("最小", "成交额",
     "SELECT ROUND(MIN(pay_amount),2) FROM v_retail_order "
     "WHERE order_date BETWEEN ? AND ? AND region_name=?", "pay_amount"),
    ("最小", "销量",
     "SELECT MIN(qty) FROM v_retail_order "
     "WHERE order_date BETWEEN ? AND ? AND region_name=?", "sales_qty"),
]


@pytest.mark.parametrize("word,alias,truth_sql,metric", AGG_CASES,
                         ids=[f"{w}{a}" for w, a, _, _ in AGG_CASES])
def test_aggregate_factor_accuracy(chat, word, alias, truth_sql, metric):
    """聚合词命中指标后，数值必须等于独立真值（SUM/AVG/MAX/MIN 同一断言）。"""
    resp = chat("U001", f"{WIN[0]} {REGION}区域{word}{alias}")
    assert resp["answer_type"] == "result", resp.get("text")
    assert metric in resp["plan"]["metrics"], (alias, resp["plan"]["metrics"])

    with sqlite3.connect(db_path()) as conn:
        expected = conn.execute(truth_sql, (*WIN, REGION)).fetchone()[0]
    got = resp["rows"][0][-1]          # 单日窗口按 date 分组 → 一行，末列是指标值
    assert got == expected, (
        f"[{word}{alias}] 聚合值不符：期望{word}={expected}，agent={got}\n"
        f"agentSQL={resp.get('sql')}\n真值SQL={truth_sql}")
