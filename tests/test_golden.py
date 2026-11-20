import json, pytest

CASES = [json.loads(l) for l in open("tests/golden/golden_cases.jsonl", encoding="utf-8")]

@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_golden(chat, case):
    ctx = case["context"]
    resp = chat(ctx["user_id"], case["query"])          # 1. 调被测入口
    v = case["verdict"]
    assert resp["answer_type"] == v["answer_type"]       # 2. 硬断言：答案类型
    for frag in v["sql_must"]:
        assert frag in (resp.get("sql") or "")           # 3. 硬断言：SQL 片段
    if v["rows_exact"]:
        got = {str(r[0]): r[1] for r in resp.get("rows", [])}
        assert got == v["rows_exact"]                    # 4. 硬断言：冻结黄金值
