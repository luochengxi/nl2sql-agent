import sqlite3
import yaml
import os
import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.core.config import ROOT, db_path,business_lines
from query_spec import ANCHOR_T
from src.core.config import settings

# 真值 oracle 直接用语义层的指标公式，保证和 agent 用的是同一套口径
with open(os.path.join(ROOT, "config", "semantic_layer.yaml"), "r", encoding="utf-8") as f:
    _SEMANTIC_LAYER = yaml.safe_load(f)


# 冻结 T：让被测系统和 spec 用同一个「今天」，杜绝 today=null 漂移
settings()["time"]["today"] = ANCHOR_T


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


@pytest.fixture
def chat(client):
    """黑盒入口：只调 API，不看内部实现。"""
    def _chat(user_id, query, session_id=None):
        payload = {"user_id": user_id, "query": query}
        if session_id:
            payload["session_id"] = session_id
        return client.post("/api/chat", json=payload).json()
    return _chat


@pytest.fixture
def db_calculation():
    """自动计算数仓数据。"""
    def _db_calculation(view, metric_id, dim_col, win, scope_sql="", scope_params=()):
        expr = _SEMANTIC_LAYER["metrics"][metric_id]["expr"]
        date_col = "order_date"

        # 日期列：按视图反查业务线配置，保证与 agent 编译口径一致
        date_col = "order_date"
        for bl in business_lines().get("business_lines", {}).values():
            if bl.get("view") == view:
                date_col = bl.get("date_column", "order_date")
                break
        sql = f"SELECT {dim_col} AS k, {expr} AS v FROM {view} " \
              f"WHERE {date_col} BETWEEN ? AND ?"
        params = [win[0], win[1]]
        if scope_sql:
            sql += f" AND {scope_sql}"
            params += list(scope_params)
        sql += f" GROUP BY {dim_col}"
        with sqlite3.connect(db_path()) as conn:
            conn.row_factory = sqlite3.Row
            result = {r["k"]: r["v"] for r in conn.execute(sql, params)}
            return result,sql
    return _db_calculation


#
# @pytest.fixture
# def date_processing():
#     def _date_processing(lable):
#         if lable in _SEMANTIC_LAYER["date_processing"]:
