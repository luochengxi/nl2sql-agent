import json, pytest
CASES = [json.loads(l) for l in open("tests/golden/golden_cases_fc100.jsonl", encoding="utf-8")]

@pytest.fixture
def chat_fc(client):
    def _chat(u, q):
        body = client.post("/api/chat-fc", json={"user_id": u, "query": q}).json()
        tr = client.get(f"/api/trace/{body['trace_id']}").json()
        return body, tr
    return _chat

@pytest.mark.parametrize("case", [c for c in CASES if c["expected_action"] == "result"],
                         ids=lambda c: c["id"])
def test_fc_result(chat_fc, case):
    resp, tr = chat_fc(case["context"]["user_id"], case["query"])
    v = case["verdict"]
    assert resp["answer_type"] == v["answer_type"]                    # 硬：答案类型
    for f in v["sql_must"]:     assert f in (resp.get("sql") or "")    # 硬：SQL 片段
    for f in v["sql_must_not"]: assert f not in (resp.get("sql") or "")
    if v["rows_exact"] is not None:                                    # 硬：冻结真值
        got = {str(r[0]): r[1] for r in resp.get("rows", [])}
        assert got == v["rows_exact"]
    assert not [r for r in tr if r["kind"] == "tool_call"
                and r["status"] in v["no_tool_status"]]               # 硬：执行状态

def assert_case(case, resp, tr):
    """一条 golden case 的完整判定：8 个 verdict 字段全覆盖。"""
    v = case["verdict"]
    txt = json.dumps(resp, ensure_ascii=False)          # 全响应文本，供泄露检查

    # ===== 结果层（读 resp）=====
    assert resp["answer_type"] == v["answer_type"], "answer_type 不符"
    sql = resp.get("sql") or ""
    for f in v["sql_must"]:     assert f in sql, f"SQL 缺 {f}"
    for f in v["sql_must_not"]: assert f not in sql, f"SQL 含禁词 {f}"
    for f in v["forbidden_text"]:                       # ← 你漏的①：泄露检查
        assert f not in txt, f"响应泄露 {f}"
    if v["rows_exact"] is not None:
        got = {str(r[0]): r[1] for r in resp.get("rows", [])}
        assert got == v["rows_exact"], "冻结真值不符"
    if v["row_count"] is not None:                      # ← 你漏的②：行数
        assert resp["row_count"] == v["row_count"]

    # ===== 工具层（读 trace）=====
    calls = [r for r in tr if r["kind"] == "tool_call"]
    chosen = {n for r in tr if r["kind"] == "decision" for n in r["selected"]}

    assert not (set(v["tools_must_not"]) & chosen), "调了禁用工具"   # ← 漏③
    assert not [c for c in calls
                if c["status"] in v["no_tool_status"]], "工具执行报错"

    seq = [c["tool_name"] for c in calls]               # ← 漏④：顺序/依赖
    for exp in case["expected_tools"]:
        hit = [c for c in calls if c["tool_name"] == exp["name"]]
        if exp.get("required"):
            assert hit, f"缺工具 {exp['name']}"
        for k, want in exp.get("args_contract", {}).items():   # ← 漏⑤：参数
            assert hit and hit[0]["tool_args"].get(k) == want, \
                f"{exp['name']}.{k} 不符"
        if exp.get("depends_on"):
            assert seq.index(exp["depends_on"]) < seq.index(exp["name"]), \
                "工具顺序颠倒"

    # tools_must 软断言单独放（见下），不塞进这里
