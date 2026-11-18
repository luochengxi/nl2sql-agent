"""筛选条件生效性测试。

方法论（每个筛选项验三层）：
  ① 识别：plan.filters 里出现对应的过滤条件；
  ② 执行：返回值 == 独立真值（真值 SQL 带同样的 WHERE）；
  ③ 有效：加筛选的值 ≠ 全量值，证明筛选没有被悄悄忽略。
真值用独立 SQL 直接打物理宽表，不读 semantic_layer 的 expr —— 断开同源。
期望按需求写：需求要求"筛选生效"，agent 做不到 = 缺陷，测试应红。
"""
import sqlite3
import pytest
from src.core.config import db_path

WIN = ("2026-11-02", "2026-11-02")   # 单日窗口 → 一行，末列即指标值
VIEW = "v_retail_order"
DATE_COL = "order_date"

# 独立真值表达式（物理列，不读语义层）
M_VALID = "SUM(CASE WHEN is_valid=1 THEN 1 ELSE 0 END)"   # 有效订单量（排除退单）
M_COUNT = "COUNT(*)"                                       # 订单量（含退单）
M_QTY   = "SUM(qty)"                                       # 销量


def truth(metric_sql, scope_sql="", scope_params=(), win=WIN):
    sql = f"SELECT {metric_sql} FROM {VIEW} WHERE {DATE_COL} BETWEEN ? AND ?"
    params = [win[0], win[1]]
    if scope_sql:
        sql += f" AND {scope_sql}"
        params += list(scope_params)
    with sqlite3.connect(db_path()) as conn:
        return conn.execute(sql, params).fetchone()[0]


def got_value(resp):
    return resp["rows"][0][-1]


# —— ① 组织范围筛选：区域 / 城市 / 门店（agent 支持）——
# (标签, 问句, filter列, filter值, 真值scope_sql, 真值scope_params)
SCOPE_CASES = [
    ("指定区域", "2026-11-02 华东有效订单量",   "region_name", "华东",   "region_name=?", ("华东",)),
    ("指定城市", "2026-11-02 上海有效订单量",   "city_name",   "上海",   "city_name=?",   ("上海",)),
    ("指定门店", "2026-11-02 SH1号店有效订单量", "store_name", "SH1号店", "store_name=?", ("SH1号店",)),
]


@pytest.mark.parametrize("label,query,fcol,fval,ssql,sparam", SCOPE_CASES,
                         ids=[c[0] for c in SCOPE_CASES])
def test_scope_filter_effective(chat, label, query, fcol, fval, ssql, sparam):
    resp = chat("U001", query)
    assert resp["answer_type"] == "result", (label, resp.get("text"))

    # ① 识别
    filters = resp["plan"]["filters"]
    assert any(f["column"] == fcol and f["value"] == fval for f in filters), (
        f"[{label}] 未识别筛选 {fcol}={fval}：{filters}")

    # ② 执行：等于带筛选的独立真值
    expected = truth(M_VALID, ssql, sparam)
    got = got_value(resp)
    assert got == expected, f"[{label}] 值不符：agent={got} 真值={expected} SQL={resp.get('sql')}"

    # ③ 有效：筛选确实收窄了结果（没被忽略）
    assert expected != truth(M_VALID), f"[{label}] 筛选值==全量，无法证明筛选生效"


# —— ② 指定时间窗口（agent 支持）——
@pytest.mark.parametrize("query,win", [
    ("2026-11-02 有效订单量",              ("2026-11-02", "2026-11-02")),
    ("2026-11-01到2026-11-02 有效订单量", ("2026-11-01", "2026-11-02")),
], ids=["单日", "两日区间"])
def test_time_window_effective(chat, query, win):
    resp = chat("U001", query)
    assert resp["answer_type"] == "result", resp.get("text")
    tr = resp["plan"]["time_range"]
    assert (tr["start"], tr["end"]) == win, (query, tr)
    assert got_value(resp) == truth(M_VALID, win=win), (query, resp.get("sql"))


# —— ③ 排除退单（作为指标口径，不是 filter）——
# —— ③ 排除退单：口径固化在指标里，只验 SQL 应用了 is_valid = 1 ——
import re  # 放到测试文件顶部


def test_exclude_refunded_effective(chat):
    """排除退单是有效订单量的固有口径，验证 agent 把它编译进 SQL。

    口径定义在 semantic_layer.yaml:
        valid_order_cnt.expr = SUM(CASE WHEN is_valid = 1 THEN 1 ELSE 0 END)
    本测试只断言 SQL 含该口径，不重复算独立真值。
    """
    resp = chat("U001", "2026-11-02 华东有效订单量")
    assert resp["answer_type"] == "result", resp.get("text")

    sql = resp.get("sql") or ""
    assert re.search(r"is_valid\s*=\s*1", sql), (
        f"有效订单量 SQL 未应用排除退单口径（缺 is_valid = 1）：\n{sql}")



# —— ④ 指定产品（需求要求支持；当前 agent 无产品 filter，预期红 = 缺陷）——
PRODUCT = "无线降噪耳机"


def test_product_filter_effective(chat):
    resp = chat("U001", f"2026-11-02 {PRODUCT}销量")
    assert resp["answer_type"] == "result", resp.get("text")
    filters = resp["plan"]["filters"]
    assert any(f["column"] == "product_name" and f["value"] == PRODUCT for f in filters), (
        f"未识别产品筛选：{filters}；当前 agent 仅 region/city/store 三种 filter（缺陷）")
    assert got_value(resp) == truth(M_QTY, "product_name=?", (PRODUCT,)), resp.get("sql")


# —— ⑤ 是否包含赠品（需求若要求支持，此测试必然红 = 数据层+意图层双重缺陷）——
# —— 赠品：数据层无 is_gift 字段，agent 必须兜底/引导，不得静默编数 ——
GIFT_QUERIES = [
    "2026-11-02 华东含赠品的有效订单量",
    "2026-11-02 只看赠品订单的销量",
    "2026-11-02 不含赠品的成交额",
]


@pytest.mark.parametrize("query", GIFT_QUERIES, ids=GIFT_QUERIES)
def test_gift_filter_does_not_fabricate(chat, query):
    """无字段的筛选：应走兜底(fallback)或澄清(clarify)，不得当成正常取数出数。"""
    resp = chat("U001", query)

    # 核心：不能返回 result。静默忽略"赠品"条件、返回全量数字 = 编造
    assert resp["answer_type"] in ("fallback", "clarify"), (
        f"[{query}] 对无法处理的赠品筛选仍出数（疑似编造）："
        f"type={resp['answer_type']} sql={resp.get('sql')} rows={resp.get('rows')}")

    # "不编造"的硬证据：没有 SQL、没有数据行
    assert not resp.get("sql"), f"不应生成 SQL：{resp.get('sql')}"
    assert not resp.get("rows"), f"不应返回数据行：{resp.get('rows')}"

    # 引导：给出说明文字 + 可选问题，把用户引回可回答的范围
    assert resp.get("text"), "兜底/引导必须给出说明文字"
    assert resp.get("suggestions"), "兜底/引导应给出可替代的问题示例"
