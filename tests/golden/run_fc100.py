"""FC 黄金集完整运行器 · 按 golden 设计意图全量判定，不做任何缺陷规避。

判定覆盖 verdict 全部 8 个字段 + expected_tools 的 args_contract/order/depends_on：
  结果层(读 resp): answer_type / sql_must / sql_must_not / rows_exact / row_count / forbidden_text
  工具层(读 trace): tools_must(软) / tools_must_not / no_tool_status / expected_tools 参数与顺序

设计立场：按 golden 声明的期望跑，agent 缺能力(如 clarify 分支)导致失败 = 真实缺陷，如实报告。

用法：
  .venv/bin/python tests/golden/run_fc100.py                 # 默认 golden_cases_fc100.jsonl，全量
  .venv/bin/python tests/golden/run_fc100.py --limit 4       # 先跑 4 条验证
  .venv/bin/python tests/golden/run_fc100.py --category anomaly
  .venv/bin/python tests/golden/run_fc100.py --skip-soft     # 跳过软断言采样(更快)
  .venv/bin/python tests/golden/run_fc100.py --golden tests/golden/golden_cases.jsonl \
      --out reports/fc100_run.json                          # 指定黄金集 + 结构化 JSON 输出
  .venv/bin/python tests/golden/run_fc100.py --trace-id HP-002  # 按 case 的 trace_id 查历史轨迹

输出（每次运行都产出，供工作台「执行中心」导入定位）：
  - 文本报告：默认 tests/golden/run_report_fc100.txt（--report 可改）
  - 结构化 JSON：--out 指定时写出，每条 case 含 verdict / fail_reasons / trace_id /
    trace_url / trace 摘要(decision[]+tool_call[]) / resp 摘要 / 耗时 —— 定位问题的全部信息
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# dotenv 加载 token（config.py 内部也会 load，这里确保 runner 独立运行时就有）
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

# 冻结时间锚点：与 golden 构建时一致，杜绝 today=null 漂移
from src.core.config import settings
ANCHOR_T = "2026-11-03"
settings()["time"]["today"] = ANCHOR_T

from fastapi.testclient import TestClient
from src.api.main import app

JL_DEFAULT = os.path.join(ROOT, "tests", "golden", "golden_cases_fc100.jsonl")
REPORT_DEFAULT = os.path.join(ROOT, "tests", "golden", "run_report_fc100.txt")
client = TestClient(app)


def run_once(case: dict):
    """一次真实 function-calling 请求，返回 (响应体, 该 turn 的全部 trace)。"""
    ctx = case["context"]
    payload = {"user_id": ctx["user_id"], "query": case["query"]}
    if ctx.get("session_id"):
        payload["session_id"] = ctx["session_id"]
    body = client.post("/api/chat-fc", json=payload).json()
    tid = body.get("trace_id", "")
    tr = client.get(f"/api/trace/{tid}").json() if tid else []
    return body, tr, tid


def trace_digest(tr: list) -> dict:
    """从双层 trace 提取定位 bug 的必要信息（决策 + 工具执行 + 耗时）。

    只保留诊断字段，不含全量 rows（trace 本身就是摘要）。
    """
    decisions, calls = [], []
    for r in tr or []:
        if r.get("kind") == "decision":
            decisions.append({
                "round": r.get("round"),
                "selected": r.get("selected"),
                "finish_reason": r.get("finish_reason"),
                "raw": r.get("raw"),
            })
        elif r.get("kind") == "tool_call":
            calls.append({
                "round": r.get("round"),
                "tool_name": r.get("tool_name"),
                "tool_args": r.get("tool_args"),
                "status": r.get("status"),
                "tool_return": r.get("tool_return"),
                "error": r.get("error"),
                "latency_ms": r.get("latency_ms"),
            })
    return {
        "decisions": decisions,
        "tool_calls": calls,
        "n_decisions": len(decisions),
        "n_tool_calls": len(calls),
    }


def resp_digest(resp: dict) -> dict:
    """响应摘要：定位用，rows 截断到前 20 行避免 JSON 过大。"""
    return {
        "answer_type": resp.get("answer_type"),
        "text": (resp.get("text") or "")[:600],
        "sql": resp.get("sql") or "",
        "row_count": resp.get("row_count"),
        "rows_head": (resp.get("rows") or [])[:20],
        "suggestions": resp.get("suggestions") or [],
        "confidence": resp.get("confidence"),
    }


def _rows_equal(got_rows, expected) -> bool:
    """维度值→度量值 比较；数值用容差，避免浮点误差误报。

    兼容两种黄金值形态：
      单指标 {"华东": 249}      -> 期望每行 [k, v]
      多指标 {"华东": [249, 12]} -> 期望每行 [k, v0, v1, ...]
    行结构不符（如缺 GROUP BY 的单列结果）判 False，不抛 IndexError——
    断言函数永远不该让整个 runner 崩掉。
    """
    if expected is None:
        return True
    multi = any(isinstance(v, list) for v in expected.values())
    got = {}
    for r in (got_rows or []):
        if not isinstance(r, (list, tuple)) or len(r) < 2:
            return False
        got[str(r[0])] = list(r[1:]) if multi else r[1]
    if set(got) != set(expected):
        return False
    for k, b in expected.items():
        a = got[k]
        if isinstance(b, list):
            if not isinstance(a, list) or len(a) != len(b):
                return False
            for x, y in zip(a, b):
                if isinstance(y, (int, float)) and isinstance(x, (int, float)):
                    if not math.isclose(x, y, rel_tol=1e-6, abs_tol=0.02):
                        return False
                elif x != y:
                    return False
        elif isinstance(b, (int, float)) and isinstance(a, (int, float)):
            if not math.isclose(a, b, rel_tol=1e-6, abs_tol=0.02):
                return False
        elif a != b:
            return False
    return True


def _decision_names(tr):
    return {n for r in tr if r.get("kind") == "decision" for n in (r.get("selected") or [])}


def check_hard(resp: dict, tr: list, case: dict) -> list[str]:
    """全部硬断言。返回失败原因列表，空列表=通过。"""
    v = case["verdict"]
    fails = []

    # ===== 结果层 =====
    if resp.get("answer_type") != v["answer_type"]:
        fails.append(f"answer_type: 期望 {v['answer_type']}，实得 {resp.get('answer_type')}")
    sql = resp.get("sql") or ""
    for frag in v.get("sql_must") or []:
        if frag not in sql:
            fails.append(f"sql_must 缺片段: {frag}")
    for frag in v.get("sql_must_not") or []:
        if frag in sql:
            fails.append(f"sql_must_not 命中: {frag}")
    if not _rows_equal(resp.get("rows"), v.get("rows_exact")):
        got = {str(r[0]): (r[1:] if len(r) > 2 else r[1])
               for r in (resp.get("rows") or []) if isinstance(r, (list, tuple)) and len(r) >= 2}
        fails.append(f"rows_exact 不符: 期望 {v.get('rows_exact')}，实得 {got}")
    if v.get("row_count") is not None and resp.get("row_count") != v["row_count"]:
        fails.append(f"row_count: 期望 {v['row_count']}，实得 {resp.get('row_count')}")
    txt = json.dumps(resp, ensure_ascii=False)
    for frag in v.get("forbidden_text") or []:
        if frag in txt:
            fails.append(f"forbidden_text 泄露: {frag}")

    # ===== 工具层 =====
    calls = [r for r in tr if r.get("kind") == "tool_call"]
    chosen = _decision_names(tr)

    banned = set(v.get("tools_must_not") or [])
    if chosen & banned:
        fails.append(f"tools_must_not 命中: {sorted(chosen & banned)}")

    bad_status = set(v.get("no_tool_status") or [])
    bad = [c for c in calls if c.get("status") in bad_status]
    if bad:
        fails.append(f"no_tool_status: {[(c.get('tool_name'), c.get('status')) for c in bad]}")

    seq = [c.get("tool_name") for c in calls]
    for exp in case.get("expected_tools") or []:
        name = exp["name"]
        hit = [c for c in calls if c.get("tool_name") == name]
        if exp.get("required") and not hit:
            fails.append(f"缺工具调用: {name}")
            continue
        for key, want in (exp.get("args_contract") or {}).items():
            if not hit:
                break
            got = (hit[0].get("tool_args") or {}).get(key)
            if got != want:
                fails.append(f"{name}.{key}: 期望 {want}，实得 {got}")
        dep = exp.get("depends_on")
        if dep and dep in seq and name in seq and seq.index(dep) >= seq.index(name):
            fails.append(f"顺序错误: {dep} 应在 {name} 之前")

    return fails


def check_soft(case: dict, n: int):
    """tools_must 软断言：n 次采样，选中率 ≥ 0.8。返回 (命中, 采样数, 是否通过)。"""
    must = set(case["verdict"].get("tools_must") or [])
    if not must:
        return None
    hits = 0
    for _ in range(n):
        _, tr, _ = run_once(case)
        if must <= _decision_names(tr):
            hits += 1
    return hits, n, hits / n >= 0.8


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=JL_DEFAULT, help="黄金集 jsonl 路径（默认 golden_cases_fc100.jsonl）")
    ap.add_argument("--out", default="", help="结构化 JSON 输出路径（供工作台执行中心导入）")
    ap.add_argument("--report", default=REPORT_DEFAULT, help="文本报告输出路径")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条(验证用)")
    ap.add_argument("--id", default="", help="按用例 id 精确筛选，如 HP-002；支持前缀模糊，如 HP-0")
    ap.add_argument("--index", type=int, default=0, help="按 1-based 序号跑单条，如 2 = 第 2 条")
    ap.add_argument("--category", default="", help="按 category 过滤")
    ap.add_argument("--soft-samples", type=int, default=5, help="软断言采样次数")
    ap.add_argument("--skip-soft", action="store_true", help="跳过软断言采样")
    args = ap.parse_args()

    jl = args.golden if os.path.isabs(args.golden) else os.path.join(ROOT, args.golden)
    with open(jl, encoding="utf-8") as f:
        cases = [json.loads(l) for l in f if l.strip()]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id or c["id"].startswith(args.id)]
    if args.index:
        if not 1 <= args.index <= len(cases):
            ap.error(f"--index {args.index} 越界，当前候选 {len(cases)} 条")
        cases = [cases[args.index - 1]]
    if args.limit:
        cases = cases[:args.limit]

    lines: list[str] = []
    def out(s: str):
        print(s)
        lines.append(s)

    out("=" * 72)
    out(f"FC 黄金集运行报告 · {datetime.now().isoformat(timespec='seconds')}")
    out(f"用例数={len(cases)}  时间锚点={ANCHOR_T}  软采样={0 if args.skip_soft else args.soft_samples}")
    out("=" * 72)

    n_pass = n_fail = 0
    fail_detail = []
    records = []  # 结构化结果，每条 case 一个
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        cid = case["id"]
        tid = ""
        tr = []
        resp = {}
        rec_exc = None
        case_t0 = time.time()
        try:
            resp, tr, tid = run_once(case)
            fails = check_hard(resp, tr, case)
        except Exception as ex:  # 单条异常只记 FAIL，不拖垮整轮
            rec_exc = f"RUNNER-ERROR {type(ex).__name__}: {ex}"
            fails = [rec_exc]
        soft = None
        if not args.skip_soft and case["verdict"].get("tools_must"):
            try:
                soft = check_soft(case, args.soft_samples)
                if soft and not soft[2]:
                    fails.append(f"tools_must 软断言 {soft[0]}/{soft[1]} < 0.8")
            except Exception as ex:
                fails.append(f"SOFT-ERROR {type(ex).__name__}: {ex}")
        mark = "PASS" if not fails else "FAIL"
        if fails:
            n_fail += 1
            fail_detail.append((cid, fails))
        else:
            n_pass += 1
        extra = f" | soft {soft[0]}/{soft[1]}" if soft else ""
        out(f"[{i:3}/{len(cases)}] {mark} {cid:8} {case['category']:12} {case['expected_action']:9} trace={tid}{extra}")
        for f in fails[:5]:
            out(f"          - {f}")

        # ---- 结构化记录：结果 + trace_id + 定位所需全部信息 ----
        ctx = case.get("context", {})
        focus = [x for x in case.get("tags", []) if x.startswith("focus-")]
        line = "-"
        for t in case.get("expected_tools", []):
            bl = (t.get("args_contract") or {}).get("business_line")
            if bl:
                line = bl
                break
        records.append({
            "id": cid,
            "category": case.get("category"),
            "expected_action": case.get("expected_action"),
            "query": case.get("query"),
            "focus": focus,
            "business_line": line,
            "user_id": ctx.get("user_id"),
            "note": (case.get("verdict") or {}).get("note", ""),
            "verdict": mark,
            "fail_reasons": fails,
            "trace_id": tid,
            "trace_url": f"GET /api/trace/{tid}" if tid else "",
            "curl": f'curl -s http://127.0.0.1:8000/api/trace/{tid} | python -m json.tool' if tid else "",
            "soft": ({"hits": soft[0], "samples": soft[1], "pass": soft[2]} if soft else None),
            "resp": resp_digest(resp) if resp else {},
            "trace": trace_digest(tr),
            "expected": {
                "verdict": {k: vv for k, vv in (case.get("verdict") or {}).items() if vv not in ([], None, "")},
                "expected_tools": case.get("expected_tools", []),
            },
            "runner_error": rec_exc,
            "elapsed_ms": round((time.time() - case_t0) * 1000, 1),
        })

    elapsed_total = time.time() - t0
    out("=" * 72)
    out(f"结果：PASS {n_pass} / FAIL {n_fail} / 总 {len(cases)}  耗时 {elapsed_total:.1f}s")
    out("=" * 72)
    for cid, fails in fail_detail:
        out(f"[{cid}] " + "; ".join(fails))
    out("")
    out("=== trace_id 定位索引（每条失败 case 都能凭 trace_id 还原决策与执行） ===")
    for r in records:
        if r["verdict"] == "FAIL":
            out(f"[{r['id']}] trace_id={r['trace_id']}  {r['trace_url']}")
    report = args.report if os.path.isabs(args.report) else os.path.join(ROOT, args.report)
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"文本报告已写入 {report}")

    # ---- 结构化 JSON：工作台「执行中心」导入 ----
    if args.out:
        opath = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        payload = {
            "meta": {
                "runner": "run_fc100.py",
                "golden": os.path.relpath(jl, ROOT),
                "golden_name": os.path.basename(jl),
                "run_at": datetime.now().isoformat(timespec="seconds"),
                "anchor_t": ANCHOR_T,
                "provider": "openai_like",
                "endpoint": "POST /api/chat-fc",
                "trace_endpoint": "GET /api/trace/{trace_id}",
                "soft_samples": 0 if args.skip_soft else args.soft_samples,
            },
            "summary": {
                "total": len(cases), "pass": n_pass, "fail": n_fail,
                "pass_rate": round(100.0 * n_pass / len(cases), 1) if cases else None,
                "elapsed_s": round(elapsed_total, 1),
            },
            "cases": records,
        }
        os.makedirs(os.path.dirname(opath), exist_ok=True)
        with open(opath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"结构化 JSON 已写入 {opath}（可用工作台执行中心导入）")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
