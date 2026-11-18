"""独立真值：只消费 spec，绝不读 agent 响应。
读语义层的【原始 expr】当期望公式——编译时被缺陷改了公式(D09/D10)这里不受影响。"""
import sqlite3, yaml
from src.core.config import ROOT, db_path
from query_spec import QuerySpec

with open(f"{ROOT}/config/semantic_layer.yaml", encoding="utf-8") as f:
    SEM = yaml.safe_load(f)
with open(f"{ROOT}/config/business_lines.yaml", encoding="utf-8") as f:
    BL = yaml.safe_load(f)
SCOPE_COL = BL["scope_columns"]   # {region: region_name, city: city_name, store: store_id}


def truth_for(spec: QuerySpec) -> tuple[dict, str]:
    """返回 ({维度值: 指标值}, 真值SQL)。"""
    m = SEM["metrics"][spec.metric]
    expr = m["expr"]
    bl = BL["business_lines"][m["business_line"]]
    view, date_col = bl["view"], bl["date_column"]

    # 对齐 agent：不指定维度时它默认按 date（intent.py:174）
    dim_id = spec.dim or "date"
    dim_col = date_col if dim_id == "date" else SEM["dimensions"][dim_id]["column"]

    start, end, _ = spec.window
    sql = f"SELECT {dim_col} AS k, {expr} AS v FROM {view} WHERE {date_col} BETWEEN ? AND ?"
    params = [start, end]
    if spec.scope:
        sql += f" AND {SCOPE_COL[spec.scope[0]]} = ?"
        params.append(spec.scope[1])
    sql += f" GROUP BY {dim_col} ORDER BY {dim_col}"

    with sqlite3.connect(db_path()) as conn:
        truth = {r[0]: r[1] for r in conn.execute(sql, params)}
    return truth, sql
