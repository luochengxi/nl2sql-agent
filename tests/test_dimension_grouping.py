
import pytest

WIN = ("2026-11-01", "2026-11-07")
# 维度 -> (问法, 物理列, 指标id, 视图)
DIM_CASES = {
    "区域":     ("各区域有效订单量", "region_name", "valid_order_cnt", "v_retail_order"),
    "门店":     ("各门店有效订单量", "store_name",   "valid_order_cnt", "v_retail_order"),
    "品类":     ("各品类有效订单量", "category",     "valid_order_cnt", "v_retail_order"),
    "SKU(商品)": ("各商品有效订单量", "product_name", "valid_order_cnt", "v_retail_order"),
    "城市":     ("各城市有效订单量", "city_name", "valid_order_cnt", "v_retail_order"),
    "卖家":     ("各卖家有效订单量", "seller_name", "valid_order_cnt", "v_retail_order"),
    "商户": ("各商户有效订单量", "business_name", "valid_order_cnt", "v_retail_order"),
}


@pytest.mark.parametrize("label,query,col,metric,view", [(k, *v) for k, v in DIM_CASES.items()])
def test_result_grouped_by_dimension(chat, db_calculation, label,query, col, metric, view):
    resp = chat("U001",query)
    agent_sql = resp.get("sql")
    truth, truth_sql = db_calculation(view, metric, col, WIN)
    # 1) 维度物理列真的被标出（dim_columns 是物理名，columns 是中文标签）
    assert col in resp.get("dim_columns", []),\
        f"[{label}]维度未标记：{resp.get('dim_columns')}"

    # 2) 维度值无重复 —— 真做了 GROUP BY
    dim_idx, metric_idx = 0, -1   # 约定：维度列第一列、度量最后一列
    dim_vals = [row[dim_idx] for row in resp["rows"]]
    assert len(dim_vals) == len(set(dim_vals)),\
        f"[{label}] 维度值重复，疑似未分组：{dim_vals}"

    # 3) 逐维度聚合值 == 仓库直算真值
    got = {row[dim_idx]: row[metric_idx] for row in resp["rows"]}
    assert got == truth, \
        (f"[{label}] 与真值不符\n" 
         f"--- Agent SQL ---\n{agent_sql}\n"
         f"--- 底表运行 SQL ---\n{truth_sql}\n"
         f"--- 真值 ---\n{truth}\n"
          f"--- 返回 ---\n{got}"
         )



