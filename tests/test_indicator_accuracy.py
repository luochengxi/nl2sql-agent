import pytest

from scripts.init_warehouse import REGIONS
from src.core.time_parser import parse_time

VALID_METRIC_CASES = {
    # 零售线 (view=v_retail_order, date=order_date)
    "有效订单量":   ("在日期为2026-11-02 华东区域有效订单量",     "region_name", "valid_order_cnt", "v_retail_order"),
    "订单量":       ("在日期为2026-11-02 华东区域订单量",         "region_name", "order_cnt",       "v_retail_order"),
    "销售额":       ("零售线在日期为2026-11-02 华东区域成交额",   "region_name", "pay_amount",      "v_retail_order"),
    "客单价":       ("在日期为2026-11-02 华东区域客单价",         "region_name", "aov",             "v_retail_order"),
    "退款率":       ("在日期为2026-11-02 华东区域退款率",         "region_name", "refund_rate",     "v_retail_order"),
    "销量":         ("在日期为2026-11-02 华东区域销量",           "region_name", "sales_qty",       "v_retail_order"),
    # 物流线 (view=v_logistics_ship, date=ship_date)
    "发货单量":     ("在日期为2026-11-02 华东区域发货单量",       "region_name", "shipment_cnt",     "v_logistics_ship"),
    "平均履约时效": ("在日期为2026-11-02 华东区域平均履约时效",   "region_name", "avg_delivery_hours","v_logistics_ship"),
    "及时妥投率":   ("在日期为2026-11-02 华东区域及时妥投率",     "region_name", "ontime_rate",      "v_logistics_ship"),
    # 绩效线 (view=v_performance, date=stat_date)
    "绩效销售额":   ("在日期为2026-11-02 华东区域绩效销售额",     "region_name", "perf_gmv",        "v_performance"),
    "目标达成率":   ("在日期为2026-11-02 华东区域目标达成率",     "region_name", "achieve_rate",     "v_performance"),
    "佣金":         ("在日期为2026-11-02 华东区域佣金",           "region_name", "commission",      "v_performance"),
    "目标销售额":   ("在日期为2026-11-02 华东区域目标销售额",     "region_name", "target_gmv",       "v_performance"),
}

WINDOWS = ["2026-11-02","2026-11-02"]
REGIONSNAME = ("华东",)

@pytest.mark.parametrize("label,query,col,metric,view", [(k, *v) for k, v in VALID_METRIC_CASES.items()])

def test_result_indicator_accuracy(chat, db_calculation,label, query, col, metric, view):
    resp = chat("U001",query)
    exp = parse_time("2026-11-02")
    agent_sql = resp.get("sql")
    truth, truth_sql = db_calculation(view, metric, col, (WINDOWS[0], WINDOWS[1]),"region_name = ?",REGIONSNAME)
    dim_idx = resp["dim_columns"].index("region_name")  # 找到区域列的位置
    got = {row[dim_idx]: row[-1] for row in resp["rows"]}
    assert resp["answer_type"]  == "result", (
        f"[{label}] 未正常出数：answer_type={resp['answer_type']} text={resp.get('text')}")

    assert metric in resp["plan"]["metrics"] ,(f"[{label}] 指标识别错误：期望 {metric}，实际 {resp['plan']['metrics']}")

    assert WINDOWS[0] == exp.start and  WINDOWS[1]== exp.end,(
        f"[{label}]时间窗口不符:期望{exp.start}~{exp.end},实际 {WINDOWS[0]}~{ WINDOWS[1]}"
    )

    assert truth, (
        f"[{label}] 真值为空：请确认数据已覆盖该窗口（钉死 today 并重新生成仓库）")

    assert got == truth, (
        f"[{label}] 指标值不符\n--- Agent SQL ---\n{resp.get('sql')}\n"
        f"--- 真值 SQL ---\n{truth_sql}\n--- 真值 ---\n{truth}\n--- 返回 ---\n{got}")




